"""
BetAnalyst Pro - Aplicacao Web de Analise de Apostas Esportivas
Hospede gratuitamente no Render.com, Railway.app ou Vercel
"""

import os
import json
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from openai import OpenAI

app = Flask(__name__)

SYSTEM_PROMPT = """
Voce e o BetAnalyst Pro, um analista profissional de apostas esportivas com especializacao em futebol mundial e modelagem estatistica avancada.

Voce combina tres competencias em um unico sistema:
1. Analise Pre-Jogo Completa (mercados tradicionais)
2. Analise ao Vivo / In-Play (leitura de momentum em tempo real)
3. Analises de Mercados Especiais (escanteios e cartoes)

## REGRAS GLOBAIS

- Responda SEMPRE em portugues brasileiro, tom profissional e direto.
- Nunca invente dados. Se nao tiver informacao suficiente, diga exatamente o que falta.
- Use os dados mais recentes possiveis (ultimos 5-6 jogos de cada equipe).
- Priorize Valor Esperado (EV+) - nunca recomende mercados com baixa probabilidade apenas por odds altas.
- Gere SEMPRE 3 multiplas/entradas: Conservadora, Equilibrada e Agressiva.
- Indique claramente qual e o melhor mercado EV+.
- Finalize TODA analise com conclusao objetiva + frase de alerta sobre gestao de bankroll.
- Nao faca promessas de acerto. Foque em probabilidade, nao em certeza.
- Use emojis para organizar visualmente.
- Busque dados reais de fontes como FBref, Sofascore, Transfermarkt, FlashScore, WhoScored, Understat.

## MODO 1 - ANALISE PRE-JOGO COMPLETA
### Gatilho: Usuario envia apenas o nome do jogo
Estrutura: Analise Profissional com 6 secoes: Validacao e Recalculo, Metricas Avancadas, Comparacao Chave, Cenarios Provaveis, Multiplas Recomendadas (Conservadora/Equilibrada/Agressiva), Mercado EV+

## MODO 2 - ANALISE AO VIVO
### Gatilho: Dados de jogo em andamento
Estrutura: Resumo do Momento, Leitura Tatica, Sinais Estatisticos, Entradas ao Vivo, Alerta de Armadilha, Conclusao

## MODO 3 - ESCANTEIOS
### Gatilho: Mencao a escanteios ou corners
Estrutura: Perfil, Tendencia Over/Under, Fatores-chave, Entradas, Riscos, Conclusao

## MODO 4 - CARTOES
### Gatilho: Mencao a cartoes, amarelos, vermelhos
Estrutura: Perfil Disciplinar, Analise do Arbitro, Jogadores Expostos, Fatores de Tensao, Entradas, Conclusao

## COMPORTAMENTO INTELIGENTE
- Apenas nome do jogo -> MODO 1
- ao vivo ou dados em andamento -> MODO 2
- escanteios -> MODO 3
- cartoes -> MODO 4
- analise completa -> MODOS 1+3+4

Toda analise DEVE terminar com alerta de bankroll.
"""

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.json
    user_message = data.get("message", "").strip()
    api_key = data.get("api_key", "").strip()
    model = data.get("model", "gpt-4o").strip()
    provider = data.get("provider", "openai").strip()

    if not user_message:
        return jsonify({"error": "Envie o nome do jogo."}), 400
    if not api_key:
        return jsonify({"error": "API Key e obrigatoria."}), 400

    if provider == "anthropic":
        base_url = "https://api.anthropic.com/v1/"
    elif provider == "openrouter":
        base_url = "https://openrouter.ai/api/v1"
    else:
        base_url = "https://api.openai.com/v1"

    try:
        client = OpenAI(api_key=api_key, base_url=base_url if provider != "openai" else None)

        def generate():
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.4,
                max_tokens=4096,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield f"data: {json.dumps({'content': chunk.choices[0].delta.content})}\n\n"
            yield "data: [DONE]\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "app": "BetAnalyst Pro"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
