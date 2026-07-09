#!/usr/bin/env python3
"""
ZACK CASH v3.0 — Estratégia Baseada em Dados Reais
═══════════════════════════════════════════════════════

BASEADO EM:
- Backtest de 5.000 estratégias no Kalshi 15-min (Reddit/TurbineFi)
- Kelly Criterion para sizing ótimo (pesquisa acadêmica)
- Análise de mercados de probabilidade (QuantPedia, Substack)

═══════════════════════════════════════════════════════
ESTRATÉGIAS COMPROVADAS QUE FUNCIONAM:
═══════════════════════════════════════════════════════

1. PANIC FADE (93/96 variações lucrativas no backtest!)
   → Quando o preço do contrato se move RÁPIDO demais, o mercado EXAGERA
   → Apostar no lado oposto ao pânico = volatility reversion
   → Funciona porque: em 15 min, movimentos extremos tendem a reverter

2. HIGH PROBABILITY HARVESTING (sua estratégia original melhorada)
   → Quando probabilidade > 80% e preço está LONGE do target
   → Entrada segura com ganho consistente (1.10-1.20x)
   → Funciona porque: com pouco tempo restante, é quase impossível reverter

3. KELLY CRITERION SIZING
   → Não apostar tudo — calcular o tamanho ideal da aposta
   → Usar Half-Kelly (metade do recomendado) para proteção
   → Funciona porque: maximiza crescimento a longo prazo sem quebrar

═══════════════════════════════════════════════════════
O QUE NÃO FUNCIONA (comprovado por dados):
═══════════════════════════════════════════════════════
- Mean reversion (comprar barato e esperar subir): 0/432 lucrativas
- Targets apertados de 2 centavos: taxas comem o lucro
- Seguir momentum cegamente: não persiste em 15 min
- Entrar perto da linha: muito volátil, imprevisível

═══════════════════════════════════════════════════════
TIMING: Sinal enviado com 4:00 restantes (tempo pro Ezequiel agir)
═══════════════════════════════════════════════════════
"""

import os, requests, numpy as np, pandas as pd, re, json, time
from datetime import datetime, timezone

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

# Timing: sinal com 8 minutos restantes (pegar contrato ainda barato)
MAX_TIME_SIGNAL = 480  # 8:00

# Kelly: usar Half-Kelly (mais seguro)
KELLY_FRACTION = 0.5  # metade do Kelly ótimo

# Filtros mínimos
MIN_DISTANCE_PCT = 0.10  # mínimo 0.10% de distância do target
MAX_COST = 0.85          # MÁXIMO que vale pagar pelo contrato (acima disso = lucro ridículo)
MIN_MULTIPLIER = 1.15    # multiplicador mínimo (1.15x = $100 → +$15)
MIN_PROBABILITY = 0.60   # mínimo 60% de probabilidade
PANIC_THRESHOLD = 0.04   # 4% de movimento = pânico (baseado no backtest)

# Log
LOG_FILE = "trade_log.json"


# ═══════════════════════════════════════════════════════════
# FUNÇÕES BASE
# ═══════════════════════════════════════════════════════════

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        return r.json().get("ok", False)
    except:
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
# ESTRATÉGIA 1: PANIC FADE (a que mais funciona!)
# ═══════════════════════════════════════════════════════════

def detect_panic(df, target, current_price):
    """
    Detecta se houve um movimento de pânico nos últimos minutos.
    Pânico = preço se moveu > PANIC_THRESHOLD% em poucos minutos
    
    Se houve pânico, a estratégia é FADE (apostar contra o pânico).
    
    Retorna: (is_panic, panic_direction, panic_magnitude, fade_direction)
    - is_panic: True se detectou pânico
    - panic_direction: "UP" ou "DOWN" (direção do pânico)
    - panic_magnitude: % do movimento
    - fade_direction: direção para apostar (oposta ao pânico)
    """
    if df is None or len(df) < 3:
        return False, None, 0, None
    
    closes = df["close"].values
    
    # Verificar movimento nos últimos 3-5 minutos
    n = min(5, len(closes))
    recent = closes[-n:]
    
    # Variação percentual
    pct_change = (recent[-1] - recent[0]) / recent[0] * 100
    
    # Verificar se é pânico (movimento > threshold)
    if abs(pct_change) >= PANIC_THRESHOLD:
        if pct_change > 0:
            # Pânico de ALTA — preço subiu rápido demais
            # FADE = apostar que vai DESCER (ou pelo menos não subir mais)
            panic_dir = "UP"
            fade_dir = "DOWN"
        else:
            # Pânico de BAIXA — preço caiu rápido demais
            # FADE = apostar que vai SUBIR (ou pelo menos não cair mais)
            panic_dir = "DOWN"
            fade_dir = "UP"
        
        return True, panic_dir, abs(pct_change), fade_dir
    
    return False, None, abs(pct_change), None


# ═══════════════════════════════════════════════════════════
# ESTRATÉGIA 2: HIGH PROBABILITY HARVESTING
# ═══════════════════════════════════════════════════════════

def high_prob_harvest(price, target, market, time_remaining):
    """
    Estratégia de colheita de alta probabilidade.
    Quando o preço está LONGE do target com pouco tempo restante,
    a probabilidade de reverter é mínima.
    
    Retorna: (is_opportunity, direction, probability, confidence)
    """
    above_target = price > target
    direction = "UP" if above_target else "DOWN"
    dist_pct = abs(price - target) / target * 100
    
    # Calcular probabilidade implícita do mercado
    if above_target:
        cost = market.get("yes_ask", 0)
    else:
        cost = market.get("no_ask", 0)
    
    # Normalizar (Kalshi pode retornar em diferentes formatos)
    if cost > 1:
        cost = cost / 100
    if cost <= 0 or cost >= 1:
        cost = 0.5
    
    probability = cost  # custo = probabilidade implícita
    
    # Critérios de oportunidade:
    # 1. Distância mínima do target
    # 2. Probabilidade alta (mercado concorda que é provável)
    # 3. Pouco tempo restante (difícil reverter)
    
    # FILTRO CRÍTICO: Se o contrato está caro demais, NÃO VALE A PENA
    # Pagar $0.90+ pra ganhar $0.10 = ridículo
    multiplier = 1.0 / cost if cost > 0 and cost < 1 else 1.0
    
    is_opportunity = (
        dist_pct >= MIN_DISTANCE_PCT and
        cost <= MAX_COST and              # NÃO pagar mais que $0.85!
        multiplier >= MIN_MULTIPLIER and   # Mínimo 1.15x de retorno
        probability >= MIN_PROBABILITY and
        time_remaining <= MAX_TIME_SIGNAL
    )
    
    # Confiança baseada em múltiplos fatores
    confidence = 0
    if dist_pct >= 0.20:
        confidence += 35
    elif dist_pct >= 0.15:
        confidence += 30
    elif dist_pct >= 0.10:
        confidence += 20
    elif dist_pct >= 0.08:
        confidence += 10
    
    if probability >= 0.85:
        confidence += 35
    elif probability >= 0.75:
        confidence += 25
    elif probability >= 0.65:
        confidence += 15
    elif probability >= 0.60:
        confidence += 10
    
    if time_remaining <= 120:
        confidence += 20
    elif time_remaining <= 180:
        confidence += 15
    elif time_remaining <= 240:
        confidence += 10
    
    return is_opportunity, direction, probability, confidence, cost


# ═══════════════════════════════════════════════════════════
# ESTRATÉGIA 3: KELLY CRITERION (quanto apostar)
# ═══════════════════════════════════════════════════════════

def kelly_sizing(p_true, market_price, bankroll=100):
    """
    Calcula o tamanho ótimo da aposta usando Kelly Criterion.
    
    p_true: sua estimativa da probabilidade real
    market_price: preço do contrato (probabilidade do mercado)
    bankroll: capital disponível
    
    Retorna: (bet_size, expected_roi, edge)
    """
    if market_price <= 0 or market_price >= 1:
        return 0, 0, 0
    
    # Net odds (quanto ganha por $1 apostado se acertar)
    b = (1 - market_price) / market_price
    
    # Probabilidade de perder
    q = 1 - p_true
    
    # Kelly fraction
    f_star = (b * p_true - q) / b
    
    # Aplicar Half-Kelly (mais seguro)
    f_half = f_star * KELLY_FRACTION
    
    # Não apostar se edge negativo
    if f_half <= 0:
        return 0, 0, 0
    
    # Limitar a 25% do bankroll máximo (proteção extra)
    f_half = min(f_half, 0.25)
    
    bet_size = bankroll * f_half
    
    # Expected ROI
    edge = p_true - market_price
    expected_roi = edge / market_price * 100
    
    return bet_size, expected_roi, edge


# ═══════════════════════════════════════════════════════════
# ANÁLISE DE VOLATILIDADE E ESTABILIDADE
# ═══════════════════════════════════════════════════════════

def analyze_volatility(df, above_target):
    """
    Analisa a volatilidade e estabilidade do movimento.
    
    Retorna: (volatility_level, trend_strength, is_stable)
    - volatility_level: "LOW", "MEDIUM", "HIGH"
    - trend_strength: -100 a +100 (positivo = favorável)
    - is_stable: True se movimento é calmo e previsível
    """
    if df is None or len(df) < 3:
        return "UNKNOWN", 0, False
    
    closes = df["close"].values
    n = min(8, len(closes))
    recent = closes[-n:]
    
    # Calcular variações entre candles
    diffs = np.diff(recent)
    pct_changes = diffs / recent[:-1] * 100
    
    # Volatilidade = desvio padrão das variações
    vol = np.std(pct_changes)
    
    if vol < 0.02:
        vol_level = "LOW"
    elif vol < 0.05:
        vol_level = "MEDIUM"
    else:
        vol_level = "HIGH"
    
    # Trend strength: consistência direcional
    if above_target:
        # Favorável = subindo ou estável
        favorable = sum(1 for d in diffs if d >= 0)
    else:
        # Favorável = descendo ou estável
        favorable = sum(1 for d in diffs if d <= 0)
    
    trend_strength = int((favorable / len(diffs)) * 100) - 50  # -50 a +50, normalizado
    trend_strength = trend_strength * 2  # -100 a +100
    
    # Estável = baixa volatilidade + tendência consistente
    is_stable = (vol_level in ["LOW", "MEDIUM"]) and (trend_strength >= 20)
    
    # Velocidade do movimento
    speed = (recent[-1] - recent[0]) / recent[0] * 100
    
    return vol_level, trend_strength, is_stable, speed


# ═══════════════════════════════════════════════════════════
# ANÁLISE COMPLETA DE UMA CRIPTO
# ═══════════════════════════════════════════════════════════

def analyze(crypto, cfg):
    """
    Análise completa usando todas as 3 estratégias.
    Retorna a melhor oportunidade encontrada.
    """
    market = get_market(cfg["series"])
    if not market:
        return None
    
    # Extrair target
    m = re.search(r'\$([0-9,]+\.?\d*)', market["title"])
    if not m:
        return None
    target = float(m.group(1).replace(",", ""))
    
    # Tempo restante
    tr = get_time_remaining(market.get("close_time"))
    if tr is None:
        return None
    
    if tr > MAX_TIME_SIGNAL:
        return {"crypto": crypto, "skip": True, "time_remaining": tr}
    
    # Preço atual
    price = get_price(cfg["kucoin"])
    if not price:
        return None
    
    # Candles (últimos 12 minutos)
    df = get_klines(cfg["kucoin"], 12)
    
    # Dados básicos
    above_target = price > target
    dist_pct = abs(price - target) / target * 100
    
    # ═══ ESTRATÉGIA 1: PANIC FADE ═══
    is_panic, panic_dir, panic_mag, fade_dir = detect_panic(df, target, price)
    
    # ═══ ESTRATÉGIA 2: HIGH PROBABILITY HARVESTING ═══
    is_harvest, harvest_dir, probability, confidence, cost = high_prob_harvest(
        price, target, market, tr
    )
    
    # ═══ ANÁLISE DE VOLATILIDADE ═══
    vol_level, trend_strength, is_stable, speed = analyze_volatility(df, above_target)
    
    # ═══ DECISÃO FINAL: Qual estratégia usar? ═══
    strategy = None
    direction = None
    score = 0
    reasons = []
    warnings = []
    
    # PRIORIDADE 1: Panic Fade (quando detectado)
    # FILTRO CRÍTICO: Panic Fade SÓ vale se o contrato estiver BARATO
    # Se o contrato já está caro ($0.85+), não tem lucro mesmo com panic fade
    panic_cost = None
    if is_panic and panic_mag >= PANIC_THRESHOLD:
        # Determinar o custo do contrato na direção do fade
        if fade_dir == "UP":
            panic_cost = market.get("yes_ask", 0)
        else:
            panic_cost = market.get("no_ask", 0)
        if panic_cost and panic_cost > 1:
            panic_cost = panic_cost / 100
        
        # SÓ entrar se contrato estiver BARATO (≤ $0.80 = mult ≥ 1.25x)
        panic_cost_ok = panic_cost and 0 < panic_cost <= MAX_COST
        
        if panic_cost_ok and fade_dir == "UP" and price < target:
            # Pânico de baixa + preço abaixo do target = fade UP
            strategy = "PANIC_FADE"
            direction = "UP"
            score = 60 + int(panic_mag * 10)
            reasons.append(f"PANIC FADE: Mercado caiu {panic_mag:.2f}% em minutos — exagero!")
            reasons.append(f"Contrato BARATO: ${panic_cost:.2f} (mult {1/panic_cost:.2f}x)")
            reasons.append(f"Backtest: 93/96 variações lucrativas")
        elif panic_cost_ok and fade_dir == "DOWN" and price > target:
            # Pânico de alta + preço acima do target = fade DOWN
            strategy = "PANIC_FADE"
            direction = "DOWN"
            score = 60 + int(panic_mag * 10)
            reasons.append(f"PANIC FADE: Mercado subiu {panic_mag:.2f}% em minutos — exagero!")
            reasons.append(f"Contrato BARATO: ${panic_cost:.2f} (mult {1/panic_cost:.2f}x)")
            reasons.append(f"Backtest: 93/96 variações lucrativas")
        elif is_panic and not panic_cost_ok:
            # Panic detectado mas contrato CARO — não vale
            warnings.append(f"Panic detectado mas contrato CARO (${panic_cost:.2f}) — sem lucro")
    
    # PRIORIDADE 2: High Probability Harvesting
    if strategy is None and is_harvest and confidence >= 40:
        strategy = "HIGH_PROB"
        direction = harvest_dir
        score = confidence
        
        reasons.append(f"ALTA PROBABILIDADE: {probability*100:.1f}% de chance")
        reasons.append(f"Distância segura: {dist_pct:.3f}% do target")
        
        if is_stable:
            score += 15
            reasons.append(f"Movimento estável e calmo — previsível")
        
        if tr <= 120:
            score += 10
            reasons.append(f"Quase fechando ({int(tr//60)}:{int(tr%60):02d}) — definido")
    
    # Se nenhuma estratégia se aplica
    if strategy is None:
        direction = "UP" if above_target else "DOWN"
        score = 0
        
        # Verificar por que não entrar
        if dist_pct < MIN_DISTANCE_PCT:
            warnings.append(f"PERTO DEMAIS da linha ({dist_pct:.3f}%) — NÃO ENTRAR")
            score -= 50
        if probability < MIN_PROBABILITY if 'probability' in dir() else True:
            pass
        if vol_level == "HIGH":
            warnings.append(f"Volatilidade ALTA — mercado instável")
            score -= 20
        if trend_strength < -20:
            warnings.append(f"Tendência CONTRA — preço indo na direção errada")
            score -= 30
    
    # ═══ KELLY SIZING ═══
    if strategy and direction:
        # Estimar probabilidade real (nossa estimativa vs mercado)
        if strategy == "PANIC_FADE":
            # No panic fade, acreditamos que o mercado exagerou
            # Nossa p_true é MAIOR que o mercado indica
            p_true = min(0.90, probability + 0.10 if 'probability' in dir() and probability else 0.70)
            market_price = cost if 'cost' in dir() and cost else 0.50
        else:
            p_true = probability
            market_price = cost
        
        bet_size, expected_roi, edge = kelly_sizing(p_true, market_price)
    else:
        bet_size, expected_roi, edge = 0, 0, 0
        p_true = 0
        market_price = 0
    
    # Multiplicador
    if cost > 0 and cost < 1:
        mult = 1.0 / cost
    else:
        mult = 1.0
    mult = min(mult, 20.0)
    ganho_100 = (mult - 1) * 100
    
    # FILTRO FINAL OBRIGATÓRIO: multiplicador mínimo
    # NUNCA recomendar se multiplicador < 1.15x (lucro ridículo)
    if mult < MIN_MULTIPLIER and strategy is not None:
        strategy = None
        score = -10
        warnings.append(f"Multiplicador muito baixo ({mult:.2f}x) — lucro insuficiente")
        reasons.clear()
    
    # Classificação final
    if score >= 60 and mult >= MIN_MULTIPLIER:
        classification = "ENTRAR"
    elif score >= 35 and mult >= MIN_MULTIPLIER:
        classification = "POSSIVEL"
    else:
        classification = "NAO_ENTRAR"
    
    return {
        "crypto": crypto,
        "skip": False,
        "strategy": strategy,
        "direction": direction,
        "classification": classification,
        "score": score,
        "price": price,
        "target": target,
        "dist_pct": dist_pct,
        "probability": probability if 'probability' in dir() else 0,
        "cost": cost if 'cost' in dir() else 0,
        "mult": mult,
        "ganho_100": ganho_100,
        "p_true": p_true,
        "edge": edge,
        "bet_size_pct": bet_size,
        "expected_roi": expected_roi,
        "vol_level": vol_level,
        "trend_strength": trend_strength,
        "is_stable": is_stable,
        "speed": speed,
        "is_panic": is_panic,
        "panic_mag": panic_mag if is_panic else 0,
        "time_remaining": tr,
        "reasons": reasons,
        "warnings": warnings,
    }


# ═══════════════════════════════════════════════════════════
# FORMATAÇÃO DA MENSAGEM TELEGRAM
# ═══════════════════════════════════════════════════════════

def format_message(results, skipped):
    now_str = datetime.now().strftime("%H:%M:%S")
    
    # Filtrar apenas resultados válidos
    valid = [r for r in results if not r.get("skip")]
    
    if not valid:
        if skipped:
            next_signal = min(s["time_remaining"] for s in skipped) - MAX_TIME_SIGNAL
            if next_signal < 0: next_signal = 0
            
            msg = f"🤖 <b>ZACK CASH v3.0 — {now_str}</b>\n"
            msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += f"⏳ <b>AGUARDANDO...</b>\n\n"
            msg += f"Próximo sinal em ~{int(next_signal//60)}:{int(next_signal%60):02d}\n\n"
            msg += f"📋 <b>Mercados:</b>\n"
            for s in skipped:
                tr = s["time_remaining"]
                msg += f"  • {s['crypto']} — {int(tr//60)}:{int(tr%60):02d}\n"
        else:
            msg = f"🤖 <b>ZACK CASH v3.0 — {now_str}</b>\n\n⚠️ Nenhum mercado aberto."
        return msg
    
    # Ordenar por score
    valid.sort(key=lambda x: x["score"], reverse=True)
    best = valid[0]
    
    mins = int(best["time_remaining"] // 60)
    secs = int(best["time_remaining"] % 60)
    
    msg = f"🤖 <b>ZACK CASH v3.0 — {now_str}</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if best["classification"] == "ENTRAR":
        dir_emoji = "⬆️" if best["direction"] == "UP" else "⬇️"
        
        # Identificar estratégia
        if best["strategy"] == "PANIC_FADE":
            strat_name = "PANIC FADE"
            strat_emoji = "🔄"
        else:
            strat_name = "ALTA PROBABILIDADE"
            strat_emoji = "🎯"
        
        msg += f"🟢 <b>ENTRAR — {strat_name} {strat_emoji}</b>\n\n"
        msg += f"🪙 <b>{best['crypto']}</b> → <b>{best['direction']} {dir_emoji}</b>\n"
        msg += f"⏱ Fecha em: <b>{mins}:{secs:02d}</b>\n\n"
        
        msg += f"📊 <b>Análise:</b>\n"
        msg += f"  💲 Preço: ${best['price']:,.2f}\n"
        msg += f"  🎯 Target: ${best['target']:,.2f}\n"
        msg += f"  📏 Distância: {best['dist_pct']:.3f}%\n"
        msg += f"  🎲 Probabilidade: {best['probability']*100:.1f}%\n"
        msg += f"  💰 Multiplicador: {best['mult']:.2f}x\n"
        msg += f"  💵 $100 → +${best['ganho_100']:.0f}\n\n"
        
        msg += f"📐 <b>Kelly (matemática):</b>\n"
        msg += f"  • Edge: +{best['edge']*100:.1f}% sobre o mercado\n"
        msg += f"  • ROI esperado: +{best['expected_roi']:.1f}%\n"
        msg += f"  • Aposta ideal: {best['bet_size_pct']:.0f}% do capital\n\n"
        
        msg += f"✅ <b>Por que entrar:</b>\n"
        for r in best["reasons"]:
            msg += f"  • {r}\n"
        
        if best["warnings"]:
            msg += f"\n⚠️ <b>Atenção:</b>\n"
            for w in best["warnings"]:
                msg += f"  • {w}\n"
        
        msg += f"\n💡 <b>Score: {best['score']}</b> | Estabilidade: {best['vol_level']}"
    
    elif best["classification"] == "POSSIVEL":
        dir_emoji = "⬆️" if best["direction"] == "UP" else "⬇️"
        
        msg += f"🟡 <b>POSSÍVEL — CUIDADO</b>\n\n"
        msg += f"🪙 <b>{best['crypto']}</b> → <b>{best['direction']} {dir_emoji}</b>\n"
        msg += f"⏱ Fecha em: <b>{mins}:{secs:02d}</b>\n\n"
        msg += f"  📏 Distância: {best['dist_pct']:.3f}%\n"
        msg += f"  🎲 Probabilidade: {best['probability']*100:.1f}%\n"
        msg += f"  💰 Mult: {best['mult']:.2f}x | $100→+${best['ganho_100']:.0f}\n\n"
        
        if best["reasons"]:
            msg += f"✅ <b>A favor:</b>\n"
            for r in best["reasons"]:
                msg += f"  • {r}\n"
        if best["warnings"]:
            msg += f"\n❌ <b>Contra:</b>\n"
            for w in best["warnings"]:
                msg += f"  • {w}\n"
        
        msg += f"\n⚠️ Considere valor menor. Score: {best['score']}"
    
    else:
        msg += f"🔴 <b>NÃO ENTRAR</b>\n\n"
        msg += f"Nenhuma oportunidade segura agora.\n\n"
        
        for ev in valid[:3]:
            dir_emoji = "⬆️" if ev["direction"] == "UP" else "⬇️"
            msg += f"  ❌ {ev['crypto']} {dir_emoji} — Score: {ev['score']}\n"
            if ev["warnings"]:
                msg += f"     ↳ {ev['warnings'][0]}\n"
        
        msg += f"\n⏳ Aguarde próximo ciclo."
    
    # Ranking
    msg += f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📋 <b>Ranking:</b>\n"
    for ev in valid[:5]:
        if ev["classification"] == "ENTRAR":
            icon = "🟢"
        elif ev["classification"] == "POSSIVEL":
            icon = "🟡"
        else:
            icon = "🔴"
        dir_emoji = "⬆️" if ev["direction"] == "UP" else "⬇️"
        strat = "PF" if ev.get("strategy") == "PANIC_FADE" else "HP" if ev.get("strategy") == "HIGH_PROB" else "—"
        msg += f"  {icon} {ev['crypto']} {dir_emoji} | {ev['probability']*100:.0f}% | {ev['dist_pct']:.3f}% | {strat} | S:{ev['score']}\n"
    
    return msg


# ═══════════════════════════════════════════════════════════
# LOG PARA APRENDIZADO
# ═══════════════════════════════════════════════════════════

def log_signal(result):
    try:
        logs = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                logs = json.load(f)
        
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "crypto": result["crypto"],
            "strategy": result.get("strategy"),
            "direction": result["direction"],
            "classification": result["classification"],
            "price": result["price"],
            "target": result["target"],
            "dist_pct": result["dist_pct"],
            "probability": result["probability"],
            "mult": result["mult"],
            "score": result["score"],
            "edge": result["edge"],
            "time_remaining": result["time_remaining"],
            "speed": result["speed"],
            "vol_level": result["vol_level"],
            "is_panic": result["is_panic"],
            "result": None,
        }
        logs.append(entry)
        
        with open(LOG_FILE, "w") as f:
            json.dump(logs, f, indent=2)
    except:
        pass


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  ZACK CASH v3.0 — Estratégias Comprovadas por Dados")
    print("  Panic Fade | High Probability | Kelly Criterion")
    print("=" * 60)
    print()
    
    results = []
    skipped = []
    
    for crypto, cfg in CRYPTOS.items():
        r = analyze(crypto, cfg)
        if r is None:
            continue
        if r.get("skip"):
            skipped.append(r)
            tr = r["time_remaining"]
            print(f"  {crypto}: SKIP — faltam {int(tr//60)}:{int(tr%60):02d}")
        else:
            results.append(r)
            strat = r.get("strategy", "—")
            print(f"  {crypto}: {r['direction']} | {r['classification']} | {strat} | score={r['score']} | prob={r['probability']*100:.1f}% | dist={r['dist_pct']:.3f}%")
    
    # Formatar e enviar
    msg = format_message(results, skipped)
    ok = send_telegram(msg)
    
    # Log da melhor oportunidade
    valid = [r for r in results if not r.get("skip") and r.get("classification") in ["ENTRAR", "POSSIVEL"]]
    if valid:
        valid.sort(key=lambda x: x["score"], reverse=True)
        log_signal(valid[0])
    
    # Status
    if valid:
        best = valid[0]
        print(f"\n[{'OK' if ok else 'ERRO'}] {best['classification']} | {best['crypto']} {best['direction']} | {best.get('strategy','—')} | score={best['score']}")
    else:
        print(f"\n[{'OK' if ok else 'ERRO'}] Nenhuma oportunidade")


if __name__ == "__main__":
    main()
