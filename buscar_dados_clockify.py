"""
Script de extração de dados do Clockify para o Dashboard de KPIs.
Usa a API básica (/time-entries) — funciona em todos os planos.

COMO USAR:
1. pip install requests
2. $env:CLOCKIFY_API_KEY="sua_key"; python buscar_dados_clockify.py
3. Abra dashboard.html no navegador
"""

import os, sys, json, time
from datetime import datetime, timezone
from collections import defaultdict
import requests

API_KEY = os.environ.get("CLOCKIFY_API_KEY", "COLOQUE_SUA_API_KEY_AQUI")
BASE_URL = "https://api.clockify.me/api/v1"
HEADERS = {"X-Api-Key": API_KEY, "Content-Type": "application/json"}


def checar_api_key():
    if not API_KEY or API_KEY == "COLOQUE_SUA_API_KEY_AQUI":
        print("ERRO: Defina sua API Key.")
        sys.exit(1)


def listar_workspaces():
    r = requests.get(f"{BASE_URL}/workspaces", headers=HEADERS)
    r.raise_for_status()
    return r.json()


def escolher_workspace(workspaces):
    if len(workspaces) == 1:
        return workspaces[0]
    print("\nWorkspaces encontrados:")
    for i, ws in enumerate(workspaces):
        print(f"  [{i}] {ws['name']}")
    return workspaces[int(input("Número do workspace: ").strip())]


def listar_membros(workspace_id):
    r = requests.get(f"{BASE_URL}/workspaces/{workspace_id}/users", headers=HEADERS)
    r.raise_for_status()
    return {m["id"]: m["name"] for m in r.json()}


def listar_projetos(workspace_id):
    projetos = {}
    page = 1
    while True:
        r = requests.get(
            f"{BASE_URL}/workspaces/{workspace_id}/projects",
            headers=HEADERS,
            params={"page": page, "page-size": 200, "archived": False},
        )
        r.raise_for_status()
        lote = r.json()
        if not lote:
            break
        for p in lote:
            projetos[p["id"]] = {
                "name": p["name"],
                "clientName": p.get("clientName") or "Sem cliente",
            }
        if len(lote) < 200:
            break
        page += 1
    return projetos


def buscar_entradas_usuario(workspace_id, user_id, data_inicio, data_fim):
    """Busca todas as entradas de tempo de um usuário, paginando."""
    entradas = []
    page = 1
    while True:
        r = requests.get(
            f"{BASE_URL}/workspaces/{workspace_id}/user/{user_id}/time-entries",
            headers=HEADERS,
            params={
                "start": data_inicio,
                "end": data_fim,
                "page": page,
                "page-size": 200,
            },
        )
        r.raise_for_status()
        lote = r.json()
        if not lote:
            break
        entradas.extend(lote)
        print(f"    Página {page}: {len(lote)} entradas")
        if len(lote) < 200:
            break
        page += 1
        time.sleep(0.15)
    return entradas


def segundos_para_horas(s):
    return round(s / 3600, 2)


def duracao_iso_para_segundos(duration_str):
    """Converte duração ISO 8601 (ex: PT1H30M) em segundos."""
    if not duration_str:
        return 0
    import re
    h = int(re.search(r'(\d+)H', duration_str).group(1)) if 'H' in duration_str else 0
    m = int(re.search(r'(\d+)M', duration_str).group(1)) if 'M' in duration_str else 0
    s = int(re.search(r'(\d+)S', duration_str).group(1)) if 'S' in duration_str else 0
    return h * 3600 + m * 60 + s


def processar_entradas(todas_entradas, projetos, membros):
    agregados = defaultdict(lambda: {
        "por_projeto": defaultdict(float),
        "por_colaborador": defaultdict(lambda: {
            "total": 0.0, "faturavel": 0.0, "nao_faturavel": 0.0, "entradas": 0
        }),
        "total_horas": 0.0,
        "total_faturavel": 0.0,
        "total_nao_faturavel": 0.0,
    })

    for entrada, user_id in todas_entradas:
        interval = entrada.get("timeInterval", {})
        start = interval.get("start")
        duration_str = interval.get("duration")

        if not start or not duration_str:
            continue

        segundos = duracao_iso_para_segundos(duration_str)
        if segundos <= 0:
            continue

        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        chave = f"{dt.year}-{dt.month:02d}"
        horas = segundos_para_horas(segundos)
        billable = entrada.get("billable", False)

        project_id = entrada.get("projectId")
        proj = projetos.get(project_id, {"name": "Sem projeto", "clientName": "Sem cliente"})
        chave_proj = f"{proj['clientName']} — {proj['name']}"
        nome_colab = membros.get(user_id, "Desconhecido")

        b = agregados[chave]
        b["por_projeto"][chave_proj] += horas
        b["total_horas"] += horas

        c = b["por_colaborador"][nome_colab]
        c["total"] += horas
        c["entradas"] += 1
        if billable:
            c["faturavel"] += horas
            b["total_faturavel"] += horas
        else:
            c["nao_faturavel"] += horas
            b["total_nao_faturavel"] += horas

    resultado = defaultdict(dict)
    for chave, b in agregados.items():
        ano, mes = chave.split("-")
        resultado[ano][mes] = {
            "total_horas": round(b["total_horas"], 2),
            "total_faturavel": round(b["total_faturavel"], 2),
            "total_nao_faturavel": round(b["total_nao_faturavel"], 2),
            "por_projeto": sorted(
                [{"nome": k, "horas": round(v, 2)} for k, v in b["por_projeto"].items()],
                key=lambda x: -x["horas"]
            ),
            "por_colaborador": sorted(
                [{
                    "nome": k,
                    "total": round(v["total"], 2),
                    "faturavel": round(v["faturavel"], 2),
                    "nao_faturavel": round(v["nao_faturavel"], 2),
                    "entradas": v["entradas"],
                } for k, v in b["por_colaborador"].items()],
                key=lambda x: -x["total"]
            ),
        }
    return resultado


def main():
    checar_api_key()

    print("Conectando ao Clockify...")
    workspaces = listar_workspaces()
    workspace = escolher_workspace(workspaces)
    workspace_id = workspace["id"]
    print(f"Usando workspace: {workspace['name']}")

    print("Buscando membros...")
    membros = listar_membros(workspace_id)
    print(f"  {len(membros)} membro(s) encontrado(s).")

    print("Buscando projetos...")
    projetos = listar_projetos(workspace_id)
    print(f"  {len(projetos)} projeto(s) encontrado(s).")

    data_inicio = "2026-01-01T00:00:00Z"
    data_fim = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Buscando entradas de {data_inicio} até {data_fim}...")

    todas_entradas = []
    for user_id, nome in membros.items():
        print(f"  → {nome}...")
        entradas_usuario = buscar_entradas_usuario(workspace_id, user_id, data_inicio, data_fim)
        for e in entradas_usuario:
            todas_entradas.append((e, user_id))
        print(f"     {len(entradas_usuario)} entrada(s)")

    print(f"\nTotal de entradas: {len(todas_entradas)}")
    print("Processando dados...")
    dados = processar_entradas(todas_entradas, projetos, membros)

    saida = {
        "workspace": workspace["name"],
        "gerado_em": datetime.now().isoformat(),
        "dados": dados,
    }

    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    print(f"\nPronto! Dados salvos em: {caminho}")
    print("Abra o dashboard.html no navegador.")


if __name__ == "__main__":
    main()
