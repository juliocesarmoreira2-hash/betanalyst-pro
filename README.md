# BetAnalyst Pro

Plataforma profissional de análises esportivas com IA. Oferece análises de pré-jogo, ao vivo, escanteios e cartões usando GPT-4.

## Funcionalidades

**4 modos de análise:**
- **Pré-Jogo** — Estatísticas, confrontos diretos, forma recente, lesões e motivação
- **Ao Vivo** — Momentum, posse de bola, finalizações e oportunidades
- **Escanteios** — Padrões por equipe, médias e correlação com estilo de jogo
- **Cartões** — Perfil disciplinar, histórico do árbitro e intensidade esperada

**Pagamentos integrados:**
- PIX via Mercado Pago (R$ 190,00/mês)
- Cartão de crédito via Stripe (US$ 35.00/mês)

**Stack:** Python/Flask, OpenRouter API (GPT-4o-mini), Mercado Pago SDK + Stripe, Render

## Setup Local

```bash
git clone https://github.com/juliocesarmoreira2-hash/betanalyst-pro.git
cd betanalyst-pro
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Disponível em `http://localhost:5000`.

## Deploy no Render

1. Push para GitHub
2. Crie um Web Service no Render
3. Conecte ao repositório (render.yaml configura tudo)
4. Adicione as variáveis de ambiente

Todos os direitos reservados.
