import os
import json
import time
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from flask import Flask, request, Response, render_template, jsonify, session
from functools import wraps

# ============================================================
# APP INITIALIZATION
# ============================================================
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
ACCESS_CODES = [c.strip() for c in os.environ.get('ACCESS_CODES', '').split(',') if c.strip()]
MERCADOPAGO_ACCESS_TOKEN = os.environ.get('MERCADOPAGO_ACCESS_TOKEN', '')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

# Pricing
PRICE_BRL = 190.00
PRICE_USD = 35.00
PLAN_NAME = "BetAnalyst Pro - Assinatura Mensal"

# Rate limiting (simple in-memory)
rate_limit_store = {}
RATE_LIMIT_MAX = 30  # max requests per window
RATE_LIMIT_WINDOW = 60  # seconds

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

def format_brl(value):
    """Format a float as Brazilian Real currency string: R$190,00"""
    formatted = f"{value:,.2f}"
    # Swap . and , for Brazilian format
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return formatted


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
            # Code expired, remove it
            del paid_codes[code]
            return False

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


def check_rate_limit(identifier):
    """Simple rate limiter. Returns True if allowed, False if rate limited."""
    now = time.time()
    if identifier not in rate_limit_store:
        rate_limit_store[identifier] = []

    # Clean old entries
    rate_limit_store[identifier] = [
        t for t in rate_limit_store[identifier]
        if now - t < RATE_LIMIT_WINDOW
    ]

    if len(rate_limit_store[identifier]) >= RATE_LIMIT_MAX:
        return False

    rate_limit_store[identifier].append(now)
    return True


def cleanup_expired_sessions():
    """Remove sessions older than 24 hours"""
    now = datetime.now()
    expired = []
    for token, data in active_sessions.items():
        created = datetime.fromisoformat(data['created'])
        if (now - created).total_seconds() > 86400:  # 24h
            expired.append(token)
    for token in expired:
        del active_sessions[token]


# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    # Cleanup expired sessions periodically
    cleanup_expired_sessions()

    return render_template('index.html',
        price_brl=format_brl(PRICE_BRL),
        price_usd=f"{PRICE_USD:.2f}",
        stripe_key=STRIPE_PUBLISHABLE_KEY
    )


@app.route('/api/auth', methods=['POST'])
def authenticate():
    """Authenticate with access code"""
    data = request.json or {}
    code = data.get('code', '').strip()

    if not code:
        return jsonify({
            'success': False,
            'message': 'Código de acesso não informado.'
        }), 400

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
    prompt = data.get('prompt', '').strip()
    mode = data.get('mode', 'pre-game')

    # Validate session
    if token not in active_sessions:
        return jsonify({'error': 'Sessão inválida. Faça login novamente.'}), 401

    if not prompt:
        return jsonify({'error': 'Prompt não pode estar vazio.'}), 400

    if not OPENROUTER_API_KEY:
        return jsonify({'error': 'API não configurada. Contate o administrador.'}), 500

    # Rate limiting per session token
    if not check_rate_limit(token):
        return jsonify({'error': 'Muitas requisições. Aguarde um momento.'}), 429

    # Validate mode
    valid_modes = ['pre-game', 'live', 'corners', 'cards']
    if mode not in valid_modes:
        mode = 'pre-game'

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
            logger.error(f"Analysis error: {e}")
            yield f"data: {json.dumps({'error': 'Erro na análise. Tente novamente.'})}\n\n"

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })


@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'BetAnalyst Pro',
        'version': '1.0.0',
        'api_configured': bool(OPENROUTER_API_KEY),
        'payments': {
            'pix': bool(MERCADOPAGO_ACCESS_TOKEN),
            'stripe': bool(STRIPE_SECRET_KEY)
        },
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

        email = request.json.get('email', '').strip()
        if not email:
            email = 'cliente@betanalyst.pro'

        payment_data = {
            "transaction_amount": PRICE_BRL,
            "description": PLAN_NAME,
            "payment_method_id": "pix",
            "payer": {
                "email": email
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
            logger.error(f"PIX payment creation failed: {result}")
            return jsonify({'success': False, 'error': 'Falha ao criar pagamento PIX'}), 500

    except Exception as e:
        logger.error(f"PIX payment error: {e}")
        return jsonify({'success': False, 'error': 'Erro ao processar pagamento.'}), 500


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
        logger.error(f"Stripe payment error: {e}")
        return jsonify({'success': False, 'error': 'Erro ao processar pagamento.'}), 500


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
                'payment_id': str(payment_id),
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
        logger.error(f"PIX verification error: {e}")
        return jsonify({'success': False, 'error': 'Erro ao verificar pagamento.'}), 500


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
        price_brl=format_brl(PRICE_BRL),
        price_usd=f"{PRICE_USD:.2f}",
        stripe_key=STRIPE_PUBLISHABLE_KEY,
        new_access_code=code
    )


@app.route('/payment/cancel')
def payment_cancel():
    return render_template('index.html',
        price_brl=format_brl(PRICE_BRL),
        price_usd=f"{PRICE_USD:.2f}",
        stripe_key=STRIPE_PUBLISHABLE_KEY,
        payment_cancelled=True
    )


@app.route('/api/webhook/mercadopago', methods=['POST'])
def mercadopago_webhook():
    """Handle Mercado Pago webhooks"""
    try:
        data = request.json or {}
        action = data.get('action', '')
        logger.info(f"MercadoPago webhook: action={action}")

        if action == 'payment.updated' and MERCADOPAGO_ACCESS_TOKEN:
            payment_id = data.get('data', {}).get('id')
            if payment_id:
                import mercadopago
                sdk = mercadopago.SDK(MERCADOPAGO_ACCESS_TOKEN)
                payment = sdk.payment().get(payment_id)

                if payment['response']['status'] == 'approved':
                    code = generate_access_code()
                    paid_codes[code] = {
                        'created': datetime.now().isoformat(),
                        'expires': (datetime.now() + timedelta(days=30)).isoformat(),
                        'method': 'pix_webhook',
                        'payment_id': str(payment_id),
                        'amount': PRICE_BRL
                    }
                    logger.info(f"PIX payment {payment_id} approved via webhook. Code: {code}")

        return jsonify({'received': True}), 200

    except Exception as e:
        logger.error(f"MercadoPago webhook error: {e}")
        return jsonify({'received': True}), 200


@app.route('/api/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks with signature verification"""
    try:
        payload = request.get_data(as_text=True)

        if STRIPE_WEBHOOK_SECRET and STRIPE_SECRET_KEY:
            import stripe
            stripe.api_key = STRIPE_SECRET_KEY
            sig_header = request.headers.get('Stripe-Signature', '')

            try:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, STRIPE_WEBHOOK_SECRET
                )
            except (ValueError, stripe.error.SignatureVerificationError) as e:
                logger.error(f"Stripe webhook signature verification failed: {e}")
                return jsonify({'error': 'Invalid signature'}), 400

            if event['type'] == 'checkout.session.completed':
                session_data = event['data']['object']
                code = generate_access_code()
                paid_codes[code] = {
                    'created': datetime.now().isoformat(),
                    'expires': (datetime.now() + timedelta(days=30)).isoformat(),
                    'method': 'stripe_webhook',
                    'session_id': session_data.get('id'),
                    'amount': PRICE_USD
                }
                logger.info(f"Stripe payment completed via webhook. Code: {code}")

        return jsonify({'received': True}), 200

    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        return jsonify({'received': True}), 200


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Página não encontrada'}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Erro interno do servidor'}), 500


# ===============================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting BetAnalyst Pro on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
