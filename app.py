import os
import json
import time
import hashlib
import secrets
from datetime import datetime, timedelta
from flask import Flask, request, Response, render_template, jsonify, session

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# ============================================================
# CONFIGURATION
# ============================================================
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
ACCESS_CODES = [c.strip() for c in os.environ.get('ACCESS_CODES', '').split(',') if c.strip()]
MERCADOPAGO_ACCESS_TOKEN = os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')

# Pricing
PRICE_BRL = 190.00
PRICE_USD = 35.00
PLAN_NAME = "BetAnalyst Pro - Assinatura Mensal"

# Simple in-memory store for active sessions (production would use a DB)
active_sessions = {}
# Store for generated access codes from payments
paid_codes = {}

# ============================================================
# SYSTEM PROMPT FOR BETTING ANALYSIS
# ============================================================
SYSTEM_PROMPT = """Você é o BetAnalyst Pro, um analista esportivo profissional de elite com mais de 20 anos de experiência em análise estatística de jogos e apostas esportivas.

## SUAS ESPECIALIDADES:
1. **Análise Pré-Jogo** - Estatísticas completas, confrontos diretos, forma recente, lesões, suspensões, condições climáticas, motivação, tendências históricas
2. **Análise ao Vivo / In-Play** - Leitura de momento do jogo, momentum, posse de bola, finalizações, pressão, substituições táticas
3. **Análise de Escanteios** - Padrões de escanteios por equipe, médias, tendências primeiro/segundo tempo, correlação com estilo de jogo
4. **Análise de Cartões** - Perfil disciplinar dos jogos, árbitro designado, histórico de cartões, rivalidade, intensidade esperada

## FORMATO DE RESPOSTA:
Para CADA análise, forneça:
- 📊 **Dados Estatísticos** - Números concretos e relevantes
- 🔍 **Análise Aprofundada** - Interpretação dos dados
- ⚡ **Tendências Identificadas** - Padrões encontrados
- 🎯 **Recomendação** - Sua sugestão baseada na análise (com nível de confiança: ⭐ a ⭐⭐⭐⭐⭐)
- ⚠️ **Alertas** - Fatores de risco ou variáveis que podem alterar o cenário
- 💡 **Value Bets** - Quando identificar oportunidades de valor

## REGRAS:
- Sempre baseie suas análises em dados e estatísticas reais quando disponíveis
- Seja honesto sobre incertezas e riscos
- Nunca garanta resultados - apostas envolvem risco
- Forneça análises detalhadas e profissionais
- Use português brasileiro
- Inclua odds sugeridas quando relevante
- Sempre mencione o disclaimer sobre responsabilidade em apostas

## DISCLAIMER (incluir ao final de toda análise):
⚠️ *Apostas esportivas envolvem risco. Esta análise é apenas informativa e educacional. Aposte com responsabilidade e apenas valores que pode perder. Jogo responsável.*"""


# ============================================================
# HELPER FUNCTIONS
# ============================================================
def validate_access(code):
    """Validate an access code"""
    if not code:
        return False
    code = code.strip().upper()
    # Check static access codes
    if code in [c.upper() for c in ACCESS_CODES]:
        return True
    # Check paid/generated codes
    if code in paid_codes:
        expiry = paid_codes[code].get('expires')
        if expiry and datetime.fromisoformat(expiry) > datetime.now():
            return True
        elif expiry:
            del paid_codes[code]
            return False
        return True
    return False


def generate_access_code():
    """Generate a unique access code for paid users"""
    code = 'BP' + secrets.token_hex(4).upper()
    return code


def create_session_token(access_code):
    """Create a session token for authenticated users"""
    token = secrets.token_hex(16)
    active_sessions[token] = {
        'code': access_code,
        'created': datetime.now().isoformat(),
        'analyses_count': 0
    }
    return token


# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template('index.html',
                         price_brl=f"{PRICE_BRL:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                         price_usd=f"{PRICE_USD:.2f}",
                         stripe_key=STRIPE_PUBLISHABLE_KEY)


@app.route('/api/auth', methods=['POST'])
def authenticate():
    """Authenticate with access code"""
    data = request.json or {}
    code = data.get('code', '').strip()

    if validate_access(code):
        token = create_session_token(code)
        return jsonify({
            'success': True,
            'token': token,
            'message': 'Acesso autorizado! Bem-vindo ao BetAnalyst Pro.'
        })
    return jsonify({
        'success': False,
        'message': 'Código de acesso inválido. Adquira seu acesso na página inicial.'
    }), 401


@app.route('/api/analyze', methods=['POST'])
def analyze():
    """Run betting analysis with streaming"""
    data = request.json or {}
    token = data.get('token', '')
    prompt = data.get('prompt', '')
    mode = data.get('mode', 'pre-game')

    # Validate session
    if token not in active_sessions:
        return jsonify({'error': 'Sessão inválida. Faça login novamente.'}), 401

    if not prompt:
        return jsonify({'error': 'Prompt não pode estar vazio.'}), 400

    if not OPENROUTER_API_KEY:
        return jsonify({'error': 'API não configurada. Contate o administrador.'}), 500

    # Build mode-specific prefix
    mode_prefixes = {
        'pre-game': '🏟️ [ANÁLISE PRÉ-JOGO]\n\n',
        'live': '⚡ [ANÁLISE AO VIVO / IN-PLAY]\n\n',
        'corners': '🔲 [ANÁLISE DE ESCANTEIOS]\n\n',
        'cards': '🟨 [ANÁLISE DE CARTÕES]\n\n'
    }
    prefix = mode_prefixes.get(mode, '')
    full_prompt = prefix + prompt

    # Track usage
    active_sessions[token]['analyses_count'] += 1

    def generate():
        try:
            import openai
            client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=OPENROUTER_API_KEY
            )

            stream = client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": full_prompt}
                ],
                stream=True,
                max_tokens=4000,
                temperature=0.7
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    yield f"data: {json.dumps({'content': content})}\n\n"

            yield f"data: {json.dumps({'done': True})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                   headers={
                       'Cache-Control': 'no-cache',
                       'X-Accel-Buffering': 'no',
                       'Connection': 'keep-alive'
                   })


@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'BetAnalyst Pro',
        'api_configured': bool(OPENROUTER_API_KEY),
        'active_sessions': len(active_sessions)
    })


# ============================================================
# PAYMENT ROUTES
# ============================================================

@app.route('/api/payment/create-pix', methods=['POST'])
def create_pix_payment():
    """Create a PIX payment via Mercado Pago"""
    try:
        if not MERCADOPAGO_ACCESS_TOKEN:
            # Fallback: generate code manually (for testing/initial setup)
            code = generate_access_code()
            paid_codes[code] = {
                'created': datetime.now().isoformat(),
                'expires': (datetime.now() + timedelta(days=30)).isoformat(),
                'method': 'pix_manual',
                'amount': PRICE_BRL
            }
            return jsonify({
                'success': True,
                'mode': 'manual',
                'message': f'PIX ainda não configurado. Código de teste gerado: {code}',
                'access_code': code
            })

        import mercadopago
        sdk = mercadopago.SDK(MERCADOPAGO_ACCESS_TOKEN)

        payment_data = {
            "transaction_amount": PRICE_BRL,
            "description": PLAN_NAME,
            "payment_method_id": "pix",
            "payer": {
                "email": request.json.get('email', 'cliente@betanalyst.pro')
            }
        }

        result = sdk.payment().create(payment_data)
        payment = result["response"]

        if payment.get("id"):
            return jsonify({
                'success': True,
                'payment_id': payment['id'],
                'qr_code': payment['point_of_interaction']['transaction_data']['qr_code'],
                'qr_code_base64': payment['point_of_interaction']['transaction_data']['qr_code_base64'],
                'ticket_url': payment['point_of_interaction']['transaction_data'].get('ticket_url', ''),
                'amount': PRICE_BRL
            })
        else:
            return jsonify({'success': False, 'error': 'Falha ao criar pagamento'}), 500

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/create-stripe', methods=['POST'])
def create_stripe_payment():
    """Create a Stripe checkout session"""
    try:
        if not STRIPE_SECRET_KEY:
            code = generate_access_code()
            paid_codes[code] = {
                'created': datetime.now().isoformat(),
                'expires': (datetime.now() + timedelta(days=30)).isoformat(),
                'method': 'stripe_manual',
                'amount': PRICE_USD
            }
            return jsonify({
                'success': True,
                'mode': 'manual',
                'message': f'Stripe ainda não configurado. Código de teste gerado: {code}',
                'access_code': code
            })

        import stripe
        stripe.api_key = STRIPE_SECRET_KEY

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': PLAN_NAME,
                        'description': 'Acesso mensal ao BetAnalyst Pro - Análises esportivas profissionais'
                    },
                    'unit_amount': int(PRICE_USD * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=request.host_url + 'payment/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=request.host_url + 'payment/cancel',
        )

        return jsonify({
            'success': True,
            'checkout_url': checkout_session.url,
            'session_id': checkout_session.id
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/payment/verify-pix', methods=['POST'])
def verify_pix_payment():
    """Check PIX payment status"""
    try:
        payment_id = request.json.get('payment_id')
        if not payment_id:
            return jsonify({'success': False, 'error': 'Payment ID required'}), 400

        if not MERCADOPAGO_ACCESS_TOKEN:
            return jsonify({'success': False, 'error': 'Mercado Pago não configurado'}), 500

        import mercadopago
        sdk = mercadopago.SDK(MERCADOPAGO_ACCESS_TOKEN)
        payment = sdk.payment().get(payment_id)

        if payment['response']['status'] == 'approved':
            code = generate_access_code()
            paid_codes[code] = {
                'created': datetime.now().isoformat(),
                'expires': (datetime.now() + timedelta(days=30)).isoformat(),
                'method': 'pix',
                'payment_id': payment_id,
                'amount': PRICE_BRL
            }
            return jsonify({
                'success': True,
                'status': 'approved',
                'access_code': code,
                'message': f'Pagamento aprovado! Seu código de acesso: {code}'
            })
        else:
            return jsonify({
                'success': True,
                'status': payment['response']['status'],
                'message': 'Aguardando confirmação do pagamento...'
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/payment/success')
def payment_success():
    """Handle successful Stripe payment"""
    session_id = request.args.get('session_id')
    code = generate_access_code()
    paid_codes[code] = {
        'created': datetime.now().isoformat(),
        'expires': (datetime.now() + timedelta(days=30)).isoformat(),
        'method': 'stripe',
        'session_id': session_id,
        'amount': PRICE_USD
    }
    return render_template('index.html',
                         price_brl=f"{PRICE_BRL:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                         price_usd=f"{PRICE_USD:.2f}",
                         stripe_key=STRIPE_PUBLISHABLE_KEY,
                         new_access_code=code)


@app.route('/payment/cancel')
def payment_cancel():
    return render_template('index.html',
                         price_brl=f"{PRICE_BRL:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                         price_usd=f"{PRICE_USD:.2f}",
                         stripe_key=STRIPE_PUBLISHABLE_KEY,
                         payment_cancelled=True)


@app.route('/api/webhook/mercadopago', methods=['POST'])
def mercadopago_webhook():
    """Handle Mercado Pago webhooks"""
    data = request.json or {}
    # In production: verify webhook signature, process payment
    return jsonify({'received': True}), 200


@app.route('/api/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks"""
    # In production: verify webhook signature, process payment
    return jsonify({'received': True}), 200


# ============================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
