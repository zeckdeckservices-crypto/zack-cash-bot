#!/usr/bin/env python3
"""
ZACK CASH v4.0 — LOOP AUTOMÁTICO 24H (RAILWAY)
═══════════════════════════════════════════════
Monitora todos os ciclos de 15 minutos.
Envia sinal no Telegram quando faltar 5 minutos.
Se detectar PANIC FADE, envia imediatamente.
═══════════════════════════════════════════════
"""

import time, requests, subprocess, sys, os, threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# ═══════════════════════════════════════════════
# HEALTH CHECK SERVER (mantém Railway ativo)
# ═══════════════════════════════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ZACK CASH v4.0 - RUNNING")
    def log_message(self, format, *args):
        pass  # Silenciar logs HTTP

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# ═══════════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════════
KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8964872216:AAG8dRobgqX3oB9iAsZ84d6HuWD_4SUIedw")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "8367252203"))

# Configuração de timing
SIGNAL_WINDOW = 300  # Enviar sinal quando faltar 5 minutos (300s)
CHECK_INTERVAL = 30  # Verificar a cada 30 segundos

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        return r.json().get("ok", False)
    except:
        return False

def get_time_remaining():
    """Pega o tempo restante do próximo mercado a fechar."""
    try:
        import pandas as pd
        r = requests.get(f"{KALSHI_BASE_URL}/events",
            params={"series_ticker": "KXBTC15M", "status": "open",
                    "with_nested_markets": True, "limit": 1}, timeout=10)
        d = r.json()
        events = d.get("events", [])
        if events:
            mk = events[0].get("markets", [{}])[0]
            ct = mk.get("close_time", "")
            if ct:
                close = pd.to_datetime(ct)
                if close.tzinfo is None:
                    close = close.tz_localize("UTC")
                remaining = (close - pd.Timestamp.now(tz="UTC")).total_seconds()
                return max(0, remaining)
    except:
        pass
    return None

def run_bot():
    """Executa o bot principal e retorna o resultado."""
    try:
        result = subprocess.run(
            [sys.executable, "signal_3min.py"],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        print(result.stdout)
        if result.stderr:
            print(f"STDERR: {result.stderr[:500]}")
        return True
    except Exception as e:
        print(f"ERRO ao rodar bot: {e}")
        return False

def bot_loop():
    """Loop principal do bot."""
    print("=" * 60)
    print("  ZACK CASH v4.0 — LOOP AUTOMÁTICO 24H (RAILWAY)")
    print("  Monitorando 24h | Sinal com 5 min restantes")
    print("  Panic Fade | Alta Probabilidade | Kelly Criterion")
    print("=" * 60)
    print()
    
    # Avisar no Telegram que o loop está ativo
    send_telegram(
        "🤖 <b>ZACK CASH v4.0 — ATIVO NA NUVEM</b>\n\n"
        "✅ Bot rodando 24h no Railway!\n"
        "📡 Monitorando todos os ciclos de 15 min\n"
        "⏱ Sinal enviado com 5 min restantes\n"
        "🔄 Estratégias: Panic Fade + Alta Prob + Kelly\n\n"
        "🟢 = ENTRAR | 🔴 = NÃO ENTRAR\n\n"
        "💰 Custo máximo: $0.85 (mult mínimo 1.18x)"
    )
    
    signal_sent_for_cycle = None
    
    while True:
        try:
            tr = get_time_remaining()
            
            if tr is None:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Sem mercado aberto. Aguardando 60s...")
                time.sleep(60)
                continue
            
            mins = int(tr // 60)
            secs = int(tr % 60)
            
            # Identificar o ciclo atual
            cycle_id = f"{int(tr // 900)}"
            
            # JANELA DE SINAL: entre 5:00 e 4:00 restantes
            if SIGNAL_WINDOW >= tr > (SIGNAL_WINDOW - 60):
                if signal_sent_for_cycle != cycle_id:
                    print(f"\n{'='*40}")
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] JANELA DE SINAL! {mins}:{secs:02d} restantes")
                    print(f"{'='*40}")
                    
                    run_bot()
                    signal_sent_for_cycle = cycle_id
                    
                    # Esperar até o próximo ciclo
                    wait_time = tr + 30
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Sinal enviado. Próximo em ~{int(wait_time//60)} min")
                    time.sleep(min(wait_time, 600))
                    signal_sent_for_cycle = None
                    continue
            
            # Se falta muito tempo, aguardar
            if tr > SIGNAL_WINDOW:
                wait = tr - SIGNAL_WINDOW
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Faltam {mins}:{secs:02d}. Sinal em {int(wait//60)}:{int(wait%60):02d}.")
                time.sleep(min(wait, CHECK_INTERVAL))
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Ciclo quase fechando ({mins}:{secs:02d}). Aguardando próximo...")
                time.sleep(CHECK_INTERVAL)
                
        except KeyboardInterrupt:
            print("\n\n[PARADO] Loop automático encerrado.")
            send_telegram("🛑 <b>ZACK CASH — PARADO</b>\n\nLoop automático encerrado.")
            break
        except Exception as e:
            print(f"\n[ERRO] {e}. Tentando novamente em 30s...")
            time.sleep(30)

def main():
    # Iniciar health check server em thread separada
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print(f"[HEALTH] Servidor HTTP rodando na porta {os.environ.get('PORT', 8080)}")
    
    # Iniciar loop do bot
    bot_loop()

if __name__ == "__main__":
    main()
