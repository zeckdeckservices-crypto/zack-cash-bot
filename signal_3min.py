#!/usr/bin/env python3
"""
ZACK CASH v4.2 — ENTRADA CERTA (CORRIGIDO)
═══════════════════════════════════════════════════════

CORREÇÃO PRINCIPAL:
- Analisa com 8-10 min restantes (quando contrato ainda é BARATO)
- Antes analisava com 5 min restantes (contrato já caro = mult < 1.05x)
- Agora encontra oportunidades reais com mult >= 1.15x

ESTRATÉGIA ÚNICA: ENTRADA CERTA
- Preço longe do target (distância >= 0.08%)
- Contrato barato (multiplicador >= 1.15x)
- Tendência confirmada (preço se afastando)
- Movimento estável (sem oscilação)

═══════════════════════════════════════════════════════
"""

import os, requests, numpy as np, pandas as pd, re, json, time
from datetime import datetime, timezone

# Auto-aprendizado
try:
    from tracker import (register_signal, check_pending_results, 
                         calculate_adaptive_score, get_min_score, load_weights)
    TRACKER_ENABLED = True
except ImportError:
    TRACKER_ENABLED = False
    print("[AVISO] Tracker não disponível - rodando sem aprendizado")

# ═══════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════════════════════

TELEGRAM_TOKEN = "8964872216:AAG8dRobgqX3oB9iAsZ84d6HuWD_4SUIedw"
CHAT_ID = 8367252203
KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
KUCOIN_BASE_URL = "https://api.kucoin.com"

CRYPTOS = {
    "BTC": {"series": "KXBTC15M", "kucoin": "BTC-USDT"},
    "ETH": {"series": "KXETH15M", "kucoin": "ETH-USDT"},
    "SOL": {"series": "KXSOL15M", "kucoin": "SOL-USDT"},
    "XRP": {"series": "KXXRP15M", "kucoin": "XRP-USDT"},
    "BNB": {"series": "KXBNB15M", "kucoin": "BNB-USDT"},
    "DOGE": {"series": "KXDOGE15M", "kucoin": "DOGE-USDT"},
    "HYPE": {"series": "KXHYPE15M", "kucoin": "HYPE-USDT"},
}

# ═══ TIMING ═══
# Analisar quando faltam entre 10 e 5 minutos
# Nesse range, o contrato ainda é barato E já dá pra ver tendência
MAX_TIME_SIGNAL = 840   # 14 minutos — aceita análise até 14 min restantes (pega logo que abre)
MIN_TIME_SIGNAL = 180   # 3 minutos — mínimo para o Ezequiel agir

# ═══ FILTROS (RELAXADOS — mais oportunidades) ═══
MIN_DISTANCE_PCT = 0.05  # mínimo 0.05% de distância do target
MAX_COST = 0.91          # máximo que vale pagar (1.10x)
MIN_MULTIPLIER = 1.10    # multiplicador mínimo (1.10x = $100 → +$10)
MIN_PROBABILITY = 0.50   # mínimo 50% de probabilidade implícita

# Kelly
KELLY_FRACTION = 0.5


# ═══════════════════════════════════════════════════════════
# FUNÇÕES BASE
# ═══════════════════════════════════════════════════════════

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        result = r.json()
        if result.get("ok"):
            return True
        else:
            print(f"[TELEGRAM] Erro: {result}")
            return False
    except Exception as e:
        print(f"[TELEGRAM] Exception: {e}")
        return False


def get_price(symbol):
    try:
        r = requests.get(f"{KUCOIN_BASE_URL}/api/v1/market/orderbook/level1",
                        params={"symbol": symbol}, timeout=5)
        d = r.json()
        if d.get("code") == "200000":
            return float(d["data"]["price"])
    except: pass
    return None


def get_klines(symbol, minutes=15):
    """Pega candles de 1 minuto dos últimos N minutos."""
    try:
        end = int(datetime.now().timestamp())
        start = end - (minutes * 60)
        r = requests.get(f"{KUCOIN_BASE_URL}/api/v1/market/candles",
            params={"type": "1min", "symbol": symbol, "startAt": start, "endAt": end}, timeout=10)
        d = r.json()
        if d.get("code") == "200000" and d.get("data"):
            df = pd.DataFrame(d["data"], columns=["timestamp","open","close","high","low","volume","turnover"])
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s")
            for c in ["open","close","high","low","volume"]:
                df[c] = df[c].astype(float)
            return df.sort_values("timestamp").reset_index(drop=True)
    except: pass
    return None


def get_market(series):
    """Pega mercado aberto da Kalshi."""
    try:
        r = requests.get(f"{KALSHI_BASE_URL}/events",
            params={"series_ticker": series, "status": "open",
                    "with_nested_markets": True, "limit": 1}, timeout=10)
        d = r.json()
        events = d.get("events", [])
        if events:
            e = events[0]
            m = e.get("markets", [])
            if m:
                mk = m[0]
                return {
                    "title": e.get("title", ""),
                    "yes_bid": float(mk.get("yes_bid_dollars", 0) or 0),
                    "no_bid": float(mk.get("no_bid_dollars", 0) or 0),
                    "yes_ask": float(mk.get("yes_ask_dollars", 0) or 0),
                    "no_ask": float(mk.get("no_ask_dollars", 0) or 0),
                    "close_time": mk.get("close_time", ""),
                    "ticker": mk.get("ticker", ""),
                }
    except: pass
    return None


def get_time_remaining(ct):
    try:
        if ct:
            close = pd.to_datetime(ct)
            now = pd.Timestamp.now(tz="UTC")
            if close.tzinfo is None:
                close = close.tz_localize("UTC")
            return max(0, (close - now).total_seconds())
    except: pass
    return None


# ═══════════════════════════════════════════════════════════
# ANÁLISE DE VOLATILIDADE E ESTABILIDADE
# ═══════════════════════════════════════════════════════════

def analyze_volatility(df, above_target):
    """
    Analisa volatilidade e estabilidade do movimento.
    Retorna: (volatility_level, trend_strength, is_stable, speed)
    """
    if df is None or len(df) < 3:
        return "UNKNOWN", 0, False, 0
    
    closes = df["close"].values
    n = min(8, len(closes))
    recent = closes[-n:]
    
    # Variações entre candles
    diffs = np.diff(recent)
    pct_changes = diffs / recent[:-1] * 100
    
    # Volatilidade
    vol = np.std(pct_changes)
    
    if vol < 0.02:
        vol_level = "LOW"
    elif vol < 0.05:
        vol_level = "MEDIUM"
    else:
        vol_level = "HIGH"
    
    # Trend strength
    if above_target:
        favorable = sum(1 for d in diffs if d >= 0)
    else:
        favorable = sum(1 for d in diffs if d <= 0)
    
    trend_strength = int((favorable / len(diffs)) * 100) - 50
    trend_strength = trend_strength * 2  # -100 a +100
    
    # Estável = baixa vol + tendência consistente
    is_stable = (vol_level in ["LOW", "MEDIUM"]) and (trend_strength >= 0)
    
    # Velocidade
    speed = (recent[-1] - recent[0]) / recent[0] * 100
    
    return vol_level, trend_strength, is_stable, speed


# ═══════════════════════════════════════════════════════════
# KELLY CRITERION
# ═══════════════════════════════════════════════════════════

def kelly_sizing(p_true, market_price, bankroll=100):
    if market_price <= 0 or market_price >= 1:
        return 0, 0, 0
    
    b = (1 - market_price) / market_price
    q = 1 - p_true
    f_star = (b * p_true - q) / b
    f_half = f_star * KELLY_FRACTION
    
    if f_half <= 0:
        return 0, 0, 0
    
    f_half = min(f_half, 0.25)
    bet_size = bankroll * f_half
    edge = p_true - market_price
    expected_roi = edge / market_price * 100
    
    return bet_size, expected_roi, edge


# ═══════════════════════════════════════════════════════════
# ANÁLISE COMPLETA DE UMA CRIPTO
# ═══════════════════════════════════════════════════════════

def analyze(crypto, cfg):
    """Análise completa. Retorna oportunidade ou status."""
    market = get_market(cfg["series"])
    if not market:
        return None
    
    # Extrair target do título
    m = re.search(r'\$([0-9,]+\.?\d*)', market["title"])
    if not m:
        return None
    target = float(m.group(1).replace(",", ""))
    
    # Tempo restante
    tr = get_time_remaining(market.get("close_time"))
    if tr is None:
        return None
    
    # Se falta MUITO tempo (> MAX_TIME_SIGNAL), skip
    if tr > MAX_TIME_SIGNAL:
        return {"crypto": crypto, "skip": True, "time_remaining": tr}
    
    # Se falta POUCO tempo (< MIN_TIME_SIGNAL), skip (não dá tempo de agir)
    if tr < MIN_TIME_SIGNAL:
        return {"crypto": crypto, "skip": True, "time_remaining": tr, "reason": "closing"}
    
    # Preço atual
    price = get_price(cfg["kucoin"])
    if not price:
        return None
    
    # Candles
    df = get_klines(cfg["kucoin"], 12)
    
    # Dados básicos
    above_target = price > target
    dist_pct = abs(price - target) / target * 100
    direction = "UP" if above_target else "DOWN"
    
    # Custo do contrato
    if above_target:
        cost = market.get("yes_ask", 0)
    else:
        cost = market.get("no_ask", 0)
    
    # Normalizar (Kalshi retorna em dólares, ex: 0.42 = 42 cents)
    if cost > 1:
        cost = cost / 100
    if cost <= 0 or cost >= 1:
        # Sem preço válido
        return {"crypto": crypto, "skip": False, "classification": "NAO_ENTRAR",
                "direction": direction, "price": price, "target": target,
                "dist_pct": dist_pct, "cost": cost, "mult": 1.0,
                "time_remaining": tr, "score": -50,
                "reasons": [], "warnings": ["Sem preço de contrato disponível"],
                "probability": 0, "ganho_100": 0, "vol_level": "UNKNOWN",
                "trend_strength": 0, "is_stable": False, "speed": 0,
                "edge": 0, "bet_size_pct": 0, "expected_roi": 0, "strategy": None}
    
    # Multiplicador
    mult = 1.0 / cost
    mult = min(mult, 20.0)
    ganho_100 = (mult - 1) * 100
    
    # Probabilidade implícita
    probability = cost
    
    # Análise de volatilidade
    vol_level, trend_strength, is_stable, speed = analyze_volatility(df, above_target)
    
    # ═══ DECISÃO: ENTRADA CERTA ═══
    strategy = None
    score = 0
    reasons = []
    warnings = []
    
    # Filtros básicos
    dist_ok = dist_pct >= MIN_DISTANCE_PCT
    mult_ok = mult >= MIN_MULTIPLIER
    cost_ok = cost <= MAX_COST
    trend_ok = trend_strength >= -30  # aceita tendência levemente contra
    # Aceitar vol HIGH se tendência for forte a favor (preço se afastando)
    stable_ok = is_stable or vol_level in ["LOW", "MEDIUM"] or (vol_level == "HIGH" and trend_strength >= 30)
    
    if dist_ok and mult_ok and cost_ok and trend_ok and stable_ok:
        strategy = "ENTRADA_CERTA"
        
        # Calcular score (ADAPTATIVO se tracker disponível)
        if TRACKER_ENABLED:
            score = calculate_adaptive_score(dist_pct, mult, trend_strength, vol_level, tr)
        else:
            # Score fixo (fallback)
            if dist_pct >= 0.30: score += 40
            elif dist_pct >= 0.20: score += 35
            elif dist_pct >= 0.15: score += 30
            elif dist_pct >= 0.10: score += 25
            else: score += 15
            
            if mult >= 2.0: score += 30
            elif mult >= 1.5: score += 25
            elif mult >= 1.3: score += 20
            elif mult >= 1.10: score += 15
            
            if trend_strength >= 50: score += 20
            elif trend_strength >= 20: score += 15
            elif trend_strength >= 0: score += 10
            
            if vol_level == "LOW": score += 15
            elif vol_level == "MEDIUM": score += 10
            
            if tr <= 300: score += 10
            elif tr <= 480: score += 5
        
        reasons.append(f"Distância segura: {dist_pct:.3f}% do target")
        reasons.append(f"Contrato barato: ${cost:.2f} (mult {mult:.2f}x)")
        reasons.append(f"Tendência {'favorável' if trend_strength >= 20 else 'neutra'}")
        if is_stable:
            reasons.append(f"Movimento estável — SEGURO")
        
    else:
        # Explicar por que não entrar
        if not dist_ok:
            warnings.append(f"Perto demais da linha ({dist_pct:.3f}%)")
        if not mult_ok:
            warnings.append(f"Contrato caro (${cost:.2f}, mult {mult:.2f}x)")
        if not cost_ok:
            warnings.append(f"Custo alto demais (${cost:.2f})")
        if not trend_ok:
            warnings.append(f"Tendência contra (força: {trend_strength})")
        if not stable_ok:
            warnings.append(f"Mercado instável (vol: {vol_level})")
    
    # Kelly sizing
    if strategy:
        p_true = min(0.95, probability + 0.05)  # pequeno edge sobre o mercado
        bet_size, expected_roi, edge = kelly_sizing(p_true, cost)
    else:
        bet_size, expected_roi, edge = 0, 0, 0
    
    # Classificação final — ADAPTATIVO
    min_score = get_min_score() if TRACKER_ENABLED else 50
    if strategy == "ENTRADA_CERTA" and score >= min_score:
        classification = "ENTRAR"
    else:
        classification = "NAO_ENTRAR"
    
    result = {
        "crypto": crypto,
        "skip": False,
        "strategy": strategy,
        "direction": direction,
        "classification": classification,
        "score": score,
        "price": price,
        "target": target,
        "dist_pct": dist_pct,
        "probability": probability,
        "cost": cost,
        "mult": mult,
        "ganho_100": ganho_100,
        "edge": edge,
        "bet_size_pct": bet_size,
        "expected_roi": expected_roi,
        "vol_level": vol_level,
        "trend_strength": trend_strength,
        "is_stable": is_stable,
        "speed": speed,
        "time_remaining": tr,
        "reasons": reasons,
        "warnings": warnings,
        "close_time": market.get("close_time", ""),
        "kucoin_symbol": cfg["kucoin"],
    }
    return result


# ═══════════════════════════════════════════════════════════
# FORMATAÇÃO DA MENSAGEM TELEGRAM
# ═══════════════════════════════════════════════════════════

def format_message(results, skipped):
    now_str = datetime.now().strftime("%H:%M:%S")
    
    valid = [r for r in results if not r.get("skip")]
    
    if not valid:
        # Nenhum mercado na janela de análise
        if skipped:
            msg = f"🤖 <b>ZACK CASH v4.2 — {now_str}</b>\n"
            msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += f"⏳ <b>AGUARDANDO JANELA...</b>\n\n"
            for s in skipped[:5]:
                tr = s["time_remaining"]
                reason = s.get("reason", "")
                if reason == "closing":
                    msg += f"  • {s['crypto']} — fechando ({int(tr//60)}:{int(tr%60):02d})\n"
                else:
                    msg += f"  • {s['crypto']} — {int(tr//60)}:{int(tr%60):02d} restantes\n"
        else:
            msg = f"🤖 <b>ZACK CASH v4.2 — {now_str}</b>\n\n⚠️ Nenhum mercado aberto."
        return msg, False
    
    # Ordenar por score
    valid.sort(key=lambda x: x["score"], reverse=True)
    best = valid[0]
    
    mins = int(best["time_remaining"] // 60)
    secs = int(best["time_remaining"] % 60)
    
    msg = f"🤖 <b>ZACK CASH v4.2 — {now_str}</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if best["classification"] == "ENTRAR":
        dir_emoji = "⬆️" if best["direction"] == "UP" else "⬇️"
        
        msg += f"🟢 <b>ENTRAR — ENTRADA CERTA 🎯</b>\n\n"
        msg += f"🪙 <b>{best['crypto']}</b> → <b>{best['direction']} {dir_emoji}</b>\n"
        msg += f"⏱ Fecha em: <b>{mins}:{secs:02d}</b>\n\n"
        
        msg += f"📊 <b>Análise:</b>\n"
        msg += f"  💲 Preço: ${best['price']:,.2f}\n"
        msg += f"  🎯 Target: ${best['target']:,.2f}\n"
        msg += f"  📏 Distância: {best['dist_pct']:.3f}%\n"
        msg += f"  💰 Custo: ${best['cost']:.2f} (mult {best['mult']:.2f}x)\n"
        msg += f"  💵 $100 → +${best['ganho_100']:.0f}\n\n"
        
        if best["edge"] > 0:
            msg += f"📐 <b>Kelly:</b>\n"
            msg += f"  • Edge: +{best['edge']*100:.1f}%\n"
            msg += f"  • ROI esperado: +{best['expected_roi']:.1f}%\n"
            msg += f"  • Aposta ideal: {best['bet_size_pct']:.0f}% do capital\n\n"
        
        msg += f"✅ <b>Por que entrar:</b>\n"
        for r in best["reasons"]:
            msg += f"  • {r}\n"
        
        if best["warnings"]:
            msg += f"\n⚠️ <b>Atenção:</b>\n"
            for w in best["warnings"]:
                msg += f"  • {w}\n"
        
        msg += f"\n💡 <b>Score: {best['score']}</b> | Vol: {best['vol_level']}"
        return msg, True
    
    else:
        msg += f"🔴 <b>NÃO ENTRAR</b>\n\n"
        msg += f"Nenhuma oportunidade segura agora.\n\n"
        
        for ev in valid[:4]:
            dir_emoji = "⬆️" if ev["direction"] == "UP" else "⬇️"
            tr_m = int(ev["time_remaining"] // 60)
            tr_s = int(ev["time_remaining"] % 60)
            msg += f"  ❌ {ev['crypto']} {dir_emoji} | dist:{ev['dist_pct']:.3f}% | ${ev['cost']:.2f} ({ev['mult']:.2f}x) | {tr_m}:{tr_s:02d}\n"
            if ev["warnings"]:
                msg += f"     ↳ {ev['warnings'][0]}\n"
        
        msg += f"\n⏳ Próximo ciclo em ~15 min."
        return msg, False


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  ZACK CASH v4.3 — AUTO-APRENDIZADO")
    print("  Analisa + Registra + Aprende com resultados")
    print("=" * 60)
    print()
    
    # 1. Verificar resultados pendentes (aprender com ciclos anteriores)
    if TRACKER_ENABLED:
        try:
            resolved = check_pending_results()
            if resolved:
                print(f"  [LEARN] {len(resolved)} resultados verificados")
                for r in resolved:
                    emoji = '✅' if r.get('won') else '❌'
                    print(f"    {emoji} {r['crypto']} {r['direction']} — {r.get('result', '?')}")
                print()
        except Exception as e:
            print(f"  [LEARN] Erro ao verificar: {e}")
    
    # 2. Analisar mercados atuais
    results = []
    skipped = []
    
    for crypto, cfg in CRYPTOS.items():
        r = analyze(crypto, cfg)
        if r is None:
            print(f"  {crypto}: sem dados")
            continue
        if r.get("skip"):
            skipped.append(r)
            tr = r["time_remaining"]
            reason = r.get("reason", "")
            print(f"  {crypto}: SKIP — {int(tr//60)}:{int(tr%60):02d} {'(fechando)' if reason == 'closing' else ''}")
        else:
            results.append(r)
            print(f"  {crypto}: {r['direction']} | {r['classification']} | score={r['score']} | dist={r['dist_pct']:.3f}% | ${r['cost']:.2f} ({r['mult']:.2f}x)")
    
    # 3. Formatar e enviar
    msg, is_entry = format_message(results, skipped)
    ok = send_telegram(msg)
    
    # 4. Registrar sinais ENTRAR no tracker (para verificar depois)
    if TRACKER_ENABLED:
        entries = [r for r in results if r.get("classification") == "ENTRAR"]
        for entry in entries:
            try:
                register_signal(entry)
            except Exception as e:
                print(f"  [TRACKER] Erro ao registrar: {e}")
    
    print(f"\n[{'OK' if ok else 'ERRO TELEGRAM'}] Mensagem enviada: {'ENTRAR' if is_entry else 'NÃO ENTRAR/AGUARDANDO'}")
    
    # Mostrar pesos atuais
    if TRACKER_ENABLED:
        try:
            w = load_weights()
            if w.get('total_signals', 0) > 0:
                wr = w['total_wins'] / w['total_signals'] * 100 if w['total_signals'] > 0 else 0
                print(f"  [STATS] Win rate: {wr:.0f}% ({w['total_wins']}/{w['total_signals']}) | Score min: {w['min_score_threshold']}")
        except:
            pass


if __name__ == "__main__":
    main()
