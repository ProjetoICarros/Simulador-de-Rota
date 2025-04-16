from flask import Flask, render_template, request, redirect, url_for
import pandas as pd
import folium
import bisect
import json
import os
from typing import List, Dict, Any


app = Flask(__name__)


# Dados dos postos com coordenadas
with open("postos.json", "r", encoding="utf-8") as postos:
    postos_data = json.load(postos)

# Função para criar DataFrame e calcular distâncias entre postos
def criar_dataframe_postos(postos_data: List[Dict[str, Any]]) -> pd.DataFrame:
    postos_df = pd.DataFrame(postos_data)
    postos_df['km'] = pd.to_numeric(postos_df['km'], errors='coerce').astype(int)
    postos_df['distancia_prox'] = postos_df['km'].shift(-1) - postos_df['km']
    return postos_df

# Função para identificar lacunas e melhorias
def analisar_lacunas_e_melhorias(postos_df: pd.DataFrame) -> Dict[str, Any]:
    media_distancia = postos_df['distancia_prox'].mean()
    desvio_distancia = postos_df['distancia_prox'].std().astype(int)
    postos_df['lacuna'] = postos_df['distancia_prox'] > (media_distancia + desvio_distancia)
    postos_df['aumentar_capacidade'] = postos_df['total_por_tipo'] < 2

    lacunas = []
    for _, row in postos_df[postos_df['lacuna']].iterrows():
        ponto_ideal_km = row['km'] + (row['distancia_prox'] / 2)
        lacunas.append({
            'nome': row['nome'],
            'localizacao': row['localizacao'],
            'km': row['km'],
            'distancia_prox': row['distancia_prox'],
            'sugestao_km': ponto_ideal_km
        }) 

    melhorias = postos_df[postos_df['aumentar_capacidade']][['nome', 'localizacao', 'km', 'total_por_tipo']].to_dict(orient="records")

    return {
        "media_distancia": media_distancia,
        "desvio_distancia": desvio_distancia,
        "lacunas": lacunas,
        "melhorias": melhorias
    }

# Função para criar o mapa com a rota e pontos de recarga
def criar_mapa_com_rota(postos_data: List[Dict[str, Any]], paradas: List[str]) -> folium.Map:
    mapa = folium.Map(location=[-22.9, -45.0], zoom_start=8)

    for posto in postos_data:
        cor_marcador = 'green' if posto['nome'] in paradas else 'blue'
        folium.Marker(
            location=[posto['lat'], posto['lon']],
            popup=f"{posto['nome']} - {posto['localizacao']} - {posto['km']} km",
            tooltip=posto['nome'],
            icon=folium.Icon(color=cor_marcador)
        ).add_to(mapa)

    mapa.save("static/mapa_pontos_recarga_rota.html")
    return mapa

# Função para simular a viagem considerando as condições de tráfego e consumo do veículo
def simular_viagem_com_trafego(
        veiculo_autonomia: float,
        consumo_por_km: float,
        capacidade_bateria: float,
        condicao_trafego: str,
        postos_df: pd.DataFrame,
        km_inicial: float = 0,
        km_final: float = 402,
        tipo_recarga: str = "normal"
) -> List[str]:
    ajuste_trafego = {"normal": 2.7, "moderado": 2.5, "intenso": 2.0, "congestionado": 1.9}
    autonomia_ajustada = (capacidade_bateria / consumo_por_km) * ajuste_trafego.get(condicao_trafego, 2.0)
    km_percorridos = km_inicial
    pontos_recarga: List[str] = []
    tempo_total = 0  # Em horas

    postos_df_sorted = postos_df.sort_values(by='km').reset_index(drop=True)
    km_posicoes = postos_df_sorted['km'].tolist()

    tempo_recarga = {"normal": 0.5, "rapido": 0.25}  # Tempo em horas

    while km_percorridos < km_final:
        posicao_atual = bisect.bisect_left(km_posicoes, km_percorridos)
        proximos_postos = postos_df_sorted.iloc[posicao_atual:]
        proximos_postos = proximos_postos[proximos_postos['km'] <= km_percorridos + autonomia_ajustada]

        if not proximos_postos.empty:
            posto_recarga_viagem = proximos_postos.iloc[0]
            pontos_recarga.append(posto_recarga_viagem['nome'])
            km_percorridos = posto_recarga_viagem['km'] + autonomia_ajustada
            tempo_total += tempo_recarga[tipo_recarga]
        else:
            break

    return pontos_recarga, tempo_total

@app.route("/", methods=["GET", "POST"])
def index():
    # Carrega os dados dos postos e carros uma única vez
    postos_df = criar_dataframe_postos(postos_data)
    
    # Carrega os dados dos carros (com tratamento de erro)
    try:
        with open('carros.json', encoding='utf-8') as f:
            carros = json.load(f)
    except FileNotFoundError:
        return "Erro: Arquivo 'carros.json' não encontrado.", 500
    except json.JSONDecodeError:
        return "Erro: Formato inválido no arquivo 'carros.json'.", 500

    if request.method == "POST":
        try:
            # Obtém os dados do formulário
            veiculo_autonomia = float(request.form["autonomia"].replace(',', '.'))
            consumo_por_km = float(request.form["consumo"].replace(',', '.'))
            capacidade_bateria = float(request.form["capacidade"].replace(',', '.'))
            condicao_trafego = request.form["trafego"]
        except (ValueError, KeyError) as e:
            return f"Erro nos dados do formulário: {str(e)}", 400

        # Simula a viagem
        pontos_recarga, tempo_total = simular_viagem_com_trafego(
            veiculo_autonomia=veiculo_autonomia,
            consumo_por_km=consumo_por_km,
            capacidade_bateria=capacidade_bateria,
            condicao_trafego=condicao_trafego,
            postos_df=postos_df
        )

        # Cria o mapa
        mapa_html = "mapa_pontos_recarga_rota.html"
        criar_mapa_com_rota(postos_data, pontos_recarga)

        return render_template(
            "index.html",
            carros=carros,
            pontos_recarga=pontos_recarga,
            tempo_total=tempo_total,
            mapa=mapa_html
        )

    # Caso GET: mostra o formulário inicial
    return render_template(
        "index.html",
        carros=carros,
        pontos_recarga=None,
        tempo_total=None,
        mapa=None
    )

@app.route("/relatorio")
def relatorio():
    postos_df = criar_dataframe_postos(postos_data)
    analise = analisar_lacunas_e_melhorias(postos_df)
    return render_template("relatorio.html", analise=analise)

@app.route('/adicionar', methods=['GET', 'POST'])
def adicionar_posto():
    return render_template("adicionar_posto.html")


if __name__ == "__main__":
    app.run(debug=True)
