#!/usr/bin/env python3
"""
ZACK CASH v4.2 — LOOP AUTOMÁTICO 24H (RAILWAY)
═══════════════════════════════════════════════
CORRIGIDO: Analisa com 10 min restantes (quando contrato ainda é barato)
Envia mensagem SEMPRE (confirma que está vivo)
═══════════════════════════════════════════════
"""

import time, requests, sys, os, threading, traceback
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
        status = f"ZACK CASH v4.2 - RUNNING - Last check: {datetime.now().strftime('%H:%M:%S')}"
        self.wfile.write(status.encode())
    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"[HEALTH] Servidor HTTP na porta {port}")
    server.serve_forever()

# ═══════════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════════
TELEGRAM_TOKEN = "8964872216:AAG8dRobgqX3oB9iAsZ84d6HuWD_4SUIedw"
CHAT_ID = 8367252203

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"[TELEGRAM ERRO] {e}")
        return False

def bot_loop():
    """Loop principal — roda signal_3min.py a cada ciclo de 15 min."""
    print("=" * 60)
    print("  ZACK CASH v4.2 — LOOP AUTOMÁTICO 24H")
    print("  Analisa com 10 min restantes (contrato barato)")
    print("  ENTRADA CERTA only | Mult >= 1.15x")
    print("=" * 60)
    print()
    
    # Avisar no Telegram que está ativo
    send_telegram(
        "🤖 <b>ZACK CASH v4.2 — ATIVO NA NUVEM</b>\n\n"
        "✅ Bot rodando 24h no Railway!\n"
        "📡 Monitorando todos os ciclos de 15 min\n"
        "⏱ Análise com 10 min restantes\n"
        "🎯 Estratégia: ENTRADA CERTA (alta prob + barato + longe)\n\n"
        "🟢 = ENTRAR | 🔴 = NÃO ENTRAR"
    )
    
    cycle_count = 0
    
    while True:
        try:
            # Rodar o bot diretamente (importar e executar)
            # Isso é mais confiável que subprocess
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Executando análise...")
            
            result = run_signal()
            cycle_count += 1
            
            if result == "SKIP":
                # Mercados com muito tempo restante — esperar 60s e tentar de novo
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Mercados ainda longe. Aguardando 60s...")
                time.sleep(60)
            elif result == "SENT":
                # Sinal enviado — esperar até próximo ciclo (~15 min)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Sinal enviado! Aguardando próximo ciclo (10 min)...")
                time.sleep(600)  # 10 min (novo ciclo abre a cada 15 min)
            elif result == "NO_MARKET":
                # Sem mercado aberto — esperar 30s
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Sem mercado. Aguardando 30s...")
                time.sleep(30)
            else:
                # Erro ou resultado desconhecido
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Resultado: {result}. Aguardando 30s...")
                time.sleep(30)
                
        except KeyboardInterrupt:
            print("\n[PARADO] Loop encerrado.")
            send_telegram("🛑 <b>ZACK CASH — PARADO</b>")
            break
        except Exception as e:
            print(f"\n[ERRO LOOP] {e}")
            traceback.print_exc()
            time.sleep(30)


def run_signal():
    """Executa a análise do signal_3min.py como subprocess."""
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "signal_3min.py"],
            capture_output=True, text=True, timeout=90,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        output = result.stdout
        print(output)
        if result.stderr:
            print(f"STDERR: {result.stderr[:500]}")
        
        # Interpretar resultado
        if "SKIP" in output and "faltam" in output:
            return "SKIP"
        elif "[OK]" in output or "[ERRO]" in output:
            return "SENT"
        elif "Nenhum mercado" in output:
            return "NO_MARKET"
        else:
            return "UNKNOWN"
    except subprocess.TimeoutExpired:
        print("[TIMEOUT] signal_3min.py demorou mais de 90s")
        return "TIMEOUT"
    except Exception as e:
        print(f"[ERRO] {e}")
        return "ERROR"


def main():
    # Iniciar health check em thread daemon
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Pequeno delay para garantir que o server subiu
    time.sleep(2)
    
    # Iniciar loop do bot na thread principal
    bot_loop()


if __name__ == "__main__":
    main()
