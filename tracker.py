#!/usr/bin/env python3
"""
ZACK CASH — TRACKER DE RESULTADOS + AUTO-APRENDIZADO
═══════════════════════════════════════════════════════

Funcionalidades:
1. Registra cada sinal enviado (ENTRAR/NÃO ENTRAR)
2. Verifica o resultado real após o ciclo fechar
3. Calcula track record (taxa de acerto, lucro, streaks)
4. Ajusta pesos automaticamente (aprende com acertos/erros)
5. Envia relatório diário de performance

═══════════════════════════════════════════════════════
"""

import json, os, requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════════════════════

TRACKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker_data.json")
WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learned_weights.json")
KUCOIN_BASE_URL = "https://api.kucoin.com"

# Pesos iniciais (serão ajustados pelo aprendizado)
DEFAULT_WEIGHTS = {
    "dist_weight": 1.0,       # importância da distância
    "mult_weight": 1.0,       # importância do multiplicador
    "trend_weight": 1.0,      # importância da tendência
    "stability_weight": 1.0,  # importância da estabilidade
    "time_weight": 1.0,       # importância do tempo restante
    "min_score_threshold": 50,  # score mínimo para ENTRAR
    "learning_rate": 0.05,    # velocidade de adaptação
    "total_signals": 0,
    "total_wins": 0,
    "total_losses": 0,
    "streak_wins": 0,
    "streak_losses": 0,
    "best_cryptos": {},       # taxa de acerto por crypto
    "best_hours": {},         # taxa de acerto por hora
    "last_updated": None,
}


# ═══════════════════════════════════════════════════════════
# FUNÇÕES DE PERSISTÊNCIA
# ═══════════════════════════════════════════════════════════

def load_tracker():
    """Carrega histórico de sinais."""
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"signals": [], "pending": []}


def save_tracker(data):
    """Salva histórico de sinais."""
    try:
        with open(TRACKER_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[TRACKER] Erro ao salvar: {e}")


def load_weights():
    """Carrega pesos aprendidos."""
    if os.path.exists(WEIGHTS_FILE):
        try:
            with open(WEIGHTS_FILE, "r") as f:
                w = json.load(f)
                # Garantir que todos os campos existem
                for k, v in DEFAULT_WEIGHTS.items():
                    if k not in w:
                        w[k] = v
                return w
        except:
            pass
    return DEFAULT_WEIGHTS.copy()


def save_weights(weights):
    """Salva pesos aprendidos."""
    try:
        weights["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(WEIGHTS_FILE, "w") as f:
            json.dump(weights, f, indent=2)
    except Exception as e:
        print(f"[WEIGHTS] Erro ao salvar: {e}")


# ═══════════════════════════════════════════════════════════
# REGISTRAR SINAL
# ═══════════════════════════════════════════════════════════

def register_signal(signal_data):
    """
    Registra um sinal enviado para verificação posterior.
    
    signal_data deve conter:
    - crypto, direction, classification, score
    - price, target, dist_pct, cost, mult
    - time_remaining, vol_level, trend_strength
    - close_time (quando o mercado fecha)
    """
    tracker = load_tracker()
    
    entry = {
        "id": len(tracker["signals"]) + len(tracker["pending"]) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "crypto": signal_data.get("crypto"),
        "direction": signal_data.get("direction"),
        "classification": signal_data.get("classification"),
        "score": signal_data.get("score"),
        "price_at_signal": signal_data.get("price"),
        "target": signal_data.get("target"),
        "dist_pct": signal_data.get("dist_pct"),
        "cost": signal_data.get("cost"),
        "mult": signal_data.get("mult"),
        "time_remaining": signal_data.get("time_remaining"),
        "vol_level": signal_data.get("vol_level"),
        "trend_strength": signal_data.get("trend_strength"),
        "close_time": signal_data.get("close_time"),
        "kucoin_symbol": signal_data.get("kucoin_symbol"),
        "result": None,  # será preenchido depois
        "price_at_close": None,
        "won": None,
        "profit_pct": None,
    }
    
    # Adicionar à lista de pendentes (aguardando verificação)
    tracker["pending"].append(entry)
    save_tracker(tracker)
    
    print(f"[TRACKER] Sinal #{entry['id']} registrado: {entry['crypto']} {entry['direction']} (score={entry['score']})")
    return entry["id"]


# ═══════════════════════════════════════════════════════════
# VERIFICAR RESULTADOS PENDENTES
# ═══════════════════════════════════════════════════════════

def get_price_now(symbol):
    """Pega preço atual de uma crypto."""
    try:
        r = requests.get(f"{KUCOIN_BASE_URL}/api/v1/market/orderbook/level1",
                        params={"symbol": symbol}, timeout=5)
        d = r.json()
        if d.get("code") == "200000":
            return float(d["data"]["price"])
    except:
        pass
    return None


def check_pending_results():
    """
    Verifica sinais pendentes cujo ciclo já fechou.
    Determina se o sinal foi WIN ou LOSS.
    """
    tracker = load_tracker()
    weights = load_weights()
    
    now = datetime.now(timezone.utc)
    resolved = []
    still_pending = []
    
    for signal in tracker["pending"]:
        close_time = signal.get("close_time")
        if not close_time:
            # Sem close_time, usar timestamp + 15 min como estimativa
            ts = pd.to_datetime(signal["timestamp"])
            tr = signal.get("time_remaining", 600)
            close_time = (ts + timedelta(seconds=tr)).isoformat()
        
        close_dt = pd.to_datetime(close_time)
        if close_dt.tzinfo is None:
            close_dt = close_dt.tz_localize("UTC")
        
        # Já fechou? (com margem de 60s para o preço se estabilizar)
        if now > close_dt + timedelta(seconds=60):
            # Verificar resultado
            target = signal.get("target")
            direction = signal.get("direction")
            classification = signal.get("classification")
            
            # Pegar preço de fechamento (usar preço atual como proxy se recente)
            kucoin_symbol = signal.get("kucoin_symbol")
            if kucoin_symbol:
                price_close = get_price_now(kucoin_symbol)
            else:
                # Tentar inferir símbolo
                crypto = signal.get("crypto", "")
                symbol_map = {"BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT",
                             "XRP": "XRP-USDT", "BNB": "BNB-USDT", "DOGE": "DOGE-USDT", "HYPE": "HYPE-USDT"}
                kucoin_symbol = symbol_map.get(crypto, f"{crypto}-USDT")
                price_close = get_price_now(kucoin_symbol)
            
            if price_close and target:
                # Determinar se ganhou
                if direction == "UP":
                    # Apostou que ficaria ACIMA do target
                    won = price_close > target
                else:
                    # Apostou que ficaria ABAIXO do target
                    won = price_close < target
                
                # Calcular lucro
                cost = signal.get("cost", 0.5)
                if won:
                    profit_pct = ((1.0 / cost) - 1) * 100  # lucro em %
                else:
                    profit_pct = -100  # perdeu tudo
                
                signal["price_at_close"] = price_close
                signal["won"] = won
                signal["profit_pct"] = profit_pct
                signal["result"] = "WIN" if won else "LOSS"
                signal["verified_at"] = now.isoformat()
                
                # Atualizar pesos
                if classification == "ENTRAR":
                    update_weights(weights, signal, won)
                
                resolved.append(signal)
                print(f"[TRACKER] Sinal #{signal['id']} {signal['crypto']}: {'✅ WIN' if won else '❌ LOSS'} (profit: {profit_pct:+.1f}%)")
            else:
                # Não conseguiu verificar — manter pendente se recente, senão descartar
                time_since_close = (now - close_dt).total_seconds()
                if time_since_close < 3600:  # menos de 1h
                    still_pending.append(signal)
                else:
                    signal["result"] = "EXPIRED"
                    signal["won"] = None
                    resolved.append(signal)
        else:
            still_pending.append(signal)
    
    # Atualizar tracker
    tracker["signals"].extend(resolved)
    tracker["pending"] = still_pending
    save_tracker(tracker)
    save_weights(weights)
    
    return resolved


# ═══════════════════════════════════════════════════════════
# AUTO-APRENDIZADO — AJUSTAR PESOS
# ═══════════════════════════════════════════════════════════

def update_weights(weights, signal, won):
    """
    Ajusta pesos baseado no resultado.
    Se ganhou: reforça as condições que levaram à entrada.
    Se perdeu: penaliza as condições.
    """
    lr = weights["learning_rate"]
    
    # Atualizar contadores
    weights["total_signals"] += 1
    if won:
        weights["total_wins"] += 1
        weights["streak_wins"] += 1
        weights["streak_losses"] = 0
    else:
        weights["total_losses"] += 1
        weights["streak_losses"] += 1
        weights["streak_wins"] = 0
    
    # Atualizar taxa por crypto
    crypto = signal.get("crypto", "UNKNOWN")
    if crypto not in weights["best_cryptos"]:
        weights["best_cryptos"][crypto] = {"wins": 0, "losses": 0, "total": 0}
    weights["best_cryptos"][crypto]["total"] += 1
    if won:
        weights["best_cryptos"][crypto]["wins"] += 1
    else:
        weights["best_cryptos"][crypto]["losses"] += 1
    
    # Atualizar taxa por hora
    hour = signal.get("timestamp", "")[:13]  # YYYY-MM-DDTHH
    try:
        h = str(pd.to_datetime(signal["timestamp"]).hour)
    except:
        h = "unknown"
    if h not in weights["best_hours"]:
        weights["best_hours"][h] = {"wins": 0, "losses": 0, "total": 0}
    weights["best_hours"][h]["total"] += 1
    if won:
        weights["best_hours"][h]["wins"] += 1
    else:
        weights["best_hours"][h]["losses"] += 1
    
    # Ajustar pesos das features baseado no resultado
    dist_pct = signal.get("dist_pct", 0)
    trend = signal.get("trend_strength", 0)
    vol = signal.get("vol_level", "MEDIUM")
    mult = signal.get("mult", 1.0)
    
    adjustment = lr if won else -lr
    
    # Se ganhou com alta distância → distância é importante
    if dist_pct >= 0.15:
        weights["dist_weight"] += adjustment * 0.5
    
    # Se ganhou com tendência forte → tendência é importante
    if trend >= 30:
        weights["trend_weight"] += adjustment * 0.5
    
    # Se ganhou com estabilidade → estabilidade é importante
    if vol in ["LOW", "MEDIUM"]:
        weights["stability_weight"] += adjustment * 0.5
    
    # Se ganhou com mult alto → mult é importante
    if mult >= 1.5:
        weights["mult_weight"] += adjustment * 0.5
    
    # Limitar pesos entre 0.3 e 3.0
    for key in ["dist_weight", "mult_weight", "trend_weight", "stability_weight", "time_weight"]:
        weights[key] = max(0.3, min(3.0, weights[key]))
    
    # Se está perdendo muito, aumentar threshold
    if weights["streak_losses"] >= 3:
        weights["min_score_threshold"] = min(80, weights["min_score_threshold"] + 5)
        print(f"[LEARN] 3 losses seguidos! Score mínimo subiu para {weights['min_score_threshold']}")
    
    # Se está ganhando muito, pode relaxar um pouco
    if weights["streak_wins"] >= 5:
        weights["min_score_threshold"] = max(40, weights["min_score_threshold"] - 5)
        print(f"[LEARN] 5 wins seguidos! Score mínimo desceu para {weights['min_score_threshold']}")


# ═══════════════════════════════════════════════════════════
# CALCULAR SCORE ADAPTATIVO (usa pesos aprendidos)
# ═══════════════════════════════════════════════════════════

def calculate_adaptive_score(dist_pct, mult, trend_strength, vol_level, time_remaining):
    """
    Calcula score usando pesos aprendidos.
    Substitui o score fixo do signal_3min.py.
    """
    weights = load_weights()
    
    score = 0
    
    # Distância (peso aprendido)
    dw = weights["dist_weight"]
    if dist_pct >= 0.30:
        score += 40 * dw
    elif dist_pct >= 0.20:
        score += 35 * dw
    elif dist_pct >= 0.15:
        score += 30 * dw
    elif dist_pct >= 0.10:
        score += 25 * dw
    elif dist_pct >= 0.05:
        score += 15 * dw
    
    # Multiplicador (peso aprendido)
    mw = weights["mult_weight"]
    if mult >= 2.0:
        score += 30 * mw
    elif mult >= 1.5:
        score += 25 * mw
    elif mult >= 1.3:
        score += 20 * mw
    elif mult >= 1.10:
        score += 15 * mw
    
    # Tendência (peso aprendido)
    tw = weights["trend_weight"]
    if trend_strength >= 50:
        score += 20 * tw
    elif trend_strength >= 20:
        score += 15 * tw
    elif trend_strength >= 0:
        score += 10 * tw
    
    # Estabilidade (peso aprendido)
    sw = weights["stability_weight"]
    if vol_level == "LOW":
        score += 15 * sw
    elif vol_level == "MEDIUM":
        score += 10 * sw
    
    # Tempo (peso aprendido)
    timew = weights["time_weight"]
    if time_remaining <= 300:
        score += 10 * timew
    elif time_remaining <= 480:
        score += 5 * timew
    
    return int(score)


def get_min_score():
    """Retorna o score mínimo adaptativo."""
    weights = load_weights()
    return weights.get("min_score_threshold", 50)


# ═══════════════════════════════════════════════════════════
# RELATÓRIO DE PERFORMANCE
# ═══════════════════════════════════════════════════════════

def generate_report():
    """
    Gera relatório de performance para enviar no Telegram.
    """
    tracker = load_tracker()
    weights = load_weights()
    
    signals = tracker.get("signals", [])
    if not signals:
        return None
    
    # Filtrar apenas sinais ENTRAR com resultado
    entries = [s for s in signals if s.get("classification") == "ENTRAR" and s.get("result") in ["WIN", "LOSS"]]
    
    if not entries:
        return "📊 <b>RELATÓRIO</b>\n\nAinda sem resultados verificados. Aguardando ciclos fecharem..."
    
    total = len(entries)
    wins = sum(1 for s in entries if s["won"])
    losses = total - wins
    win_rate = (wins / total * 100) if total > 0 else 0
    
    # Lucro acumulado (simulando $100 por entrada)
    total_profit = 0
    for s in entries:
        if s["won"]:
            total_profit += s.get("profit_pct", 0)
        else:
            total_profit -= 100  # perdeu o investido
    
    # Melhor crypto
    crypto_stats = weights.get("best_cryptos", {})
    best_crypto = None
    best_rate = 0
    for crypto, stats in crypto_stats.items():
        if stats["total"] >= 3:  # mínimo 3 sinais
            rate = stats["wins"] / stats["total"] * 100
            if rate > best_rate:
                best_rate = rate
                best_crypto = crypto
    
    # Últimos 10 resultados
    recent = entries[-10:]
    recent_str = ""
    for s in recent:
        emoji = "✅" if s["won"] else "❌"
        recent_str += f"{emoji} "
    
    msg = f"📊 <b>RELATÓRIO DE PERFORMANCE</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"📈 <b>Resultados:</b>\n"
    msg += f"  • Total de entradas: {total}\n"
    msg += f"  • Acertos: {wins} ✅\n"
    msg += f"  • Erros: {losses} ❌\n"
    msg += f"  • <b>Taxa de acerto: {win_rate:.1f}%</b>\n\n"
    
    msg += f"💰 <b>Lucro simulado:</b>\n"
    msg += f"  • Retorno acumulado: {total_profit:+.1f}%\n"
    msg += f"  • (base $100 por entrada)\n\n"
    
    if best_crypto:
        msg += f"🏆 <b>Melhor crypto:</b> {best_crypto} ({best_rate:.0f}% acerto)\n\n"
    
    msg += f"📋 <b>Últimos 10:</b> {recent_str}\n\n"
    
    # Info de aprendizado
    msg += f"🧠 <b>Aprendizado:</b>\n"
    msg += f"  • Score mínimo atual: {weights.get('min_score_threshold', 50)}\n"
    msg += f"  • Pesos: dist={weights['dist_weight']:.2f} | mult={weights['mult_weight']:.2f} | trend={weights['trend_weight']:.2f}\n"
    
    if weights["streak_wins"] >= 3:
        msg += f"  • 🔥 Sequência de {weights['streak_wins']} acertos!\n"
    if weights["streak_losses"] >= 2:
        msg += f"  • ⚠️ Sequência de {weights['streak_losses']} erros\n"
    
    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🤖 Bot se adaptando automaticamente"
    
    return msg


# ═══════════════════════════════════════════════════════════
# TESTE
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== TRACKER TEST ===")
    
    # Testar registro
    test_signal = {
        "crypto": "BTC",
        "direction": "UP",
        "classification": "ENTRAR",
        "score": 65,
        "price": 62800,
        "target": 62750,
        "dist_pct": 0.08,
        "cost": 0.60,
        "mult": 1.67,
        "time_remaining": 480,
        "vol_level": "LOW",
        "trend_strength": 40,
        "close_time": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "kucoin_symbol": "BTC-USDT",
    }
    
    register_signal(test_signal)
    
    # Verificar pendentes
    resolved = check_pending_results()
    print(f"\nResolvidos: {len(resolved)}")
    
    # Gerar relatório
    report = generate_report()
    if report:
        print(f"\n{report}")
    
    # Mostrar pesos
    weights = load_weights()
    print(f"\nPesos atuais: {json.dumps(weights, indent=2)}")
