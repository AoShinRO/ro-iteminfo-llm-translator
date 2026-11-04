# -*- coding: utf-8 -*-

# Instalação prévia necessária (Processadores sem AVX executar no prompt cmd antes de instalar o llama e rodar na GPU):

# AMD: Vulkan SDK Download: https://vulkan.lunarg.com/sdk/home#windows 
# set "VULKAN_SDK=C:/VulkanSDK/1.4.363.0"
# set "CMAKE_ARGS=-DGGML_VULKAN=ON -DGGML_NATIVE=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF -DVulkan_INCLUDE_DIR=%VULKAN_SDK%/Include -DVulkan_LIBRARY=%VULKAN_SDK%/Lib/vulkan-1.lib"
# set "FORCE_CMAKE=1"
# pip install llama-cpp-python --force-reinstall --no-cache-dir --no-binary llama-cpp-python --no-deps

# set "NPY_DISABLE_CPU_FEATURES=AVX,AVX2,FMA3,AVX512F"
# pip install numpy --no-binary numpy --force-reinstall --config-settings=setup-args="-Dcpu-baseline=none" --config-settings=setup-args="-Dcpu-dispatch=none"
# pip install customtkinter


# NVIDEA (untested):
# set "CMAKE_ARGS=-DGGML_CUDA=ON -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF -DGGML_NATIVE=OFF"
# set "FORCE_CMAKE=1"
# pip install llama-cpp-python --force-reinstall --no-cache-dir --no-binary llama-cpp-python

# Processadores com AVX rodando na CPU: pip install llama-cpp-python customtkinter

# Modelos especialistas em tradução: https://huggingface.co/models?pipeline_tag=translation&language=en,pt&sort=likes&search=gguf

import os
import re
import json
import sys
import threading
import time
import queue
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
except ImportError:
    print("[ERRO] Falta a dependência customtkinter. Instale com: pip install customtkinter")
    raise

CAMINHO_ENTRADA = ""
CAMINHO_SAIDA = ""
CAMINHO_CACHE = ""
CAMINHO_REVISAR = ""
NOME_MODELO_GGUF = ""
CAMINHO_ESTADO = ""
ENCODING_ARQUIVO = "latin-1"
QUEBRA_DE_LINHA = "\r\n"
TRADUZIR_NOMES = True
cache_traducoes = {}
para_revisar = []
llm = None
evento_stop = threading.Event()
evento_pausa = threading.Event()
evento_pausa.set()

# --- ÚLTIMAS SELEÇÕES DA GUI ---
CAMINHO_PREFERENCIAS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".tradutor_iteminfo_ultima_selecao.json"
)

def carregar_preferencias_gui():
    try:
        with open(CAMINHO_PREFERENCIAS, "r", encoding="utf-8") as f:
            dados = json.load(f)
            return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}

def salvar_preferencias_gui(iteminfo=None, modelo=None):
    dados = carregar_preferencias_gui()
    if iteminfo is not None:
        dados["arquivo_iteminfo"] = iteminfo
    if modelo is not None:
        dados["modelo_gguf"] = modelo
    temporario = CAMINHO_PREFERENCIAS + ".tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        os.replace(temporario, CAMINHO_PREFERENCIAS)
    except OSError as e:
        print(f"[AVISO] Não foi possível salvar as últimas seleções: {e}")

# O ItemInfo mistura bytes de codepages diferentes (nomes de sprite em coreano + texto em cp1252).
# latin-1 mapeia cada byte para exatamente um caractere, então ler e gravar com ele preserva,
# byte a byte, tudo o que não é traduzido (os acentos do português têm os mesmos bytes no cp1252).
# Não use detector de encoding nem errors="ignore" aqui: isso destrói os identifiedResourceName.
# Obs.: editores (Notepad++) mostram a saída como "ANSI" porque ela mistura os bytes coreanos originais
# com acentos cp1252 e isso não é mais EUC-KR válido. É o mesmo rótulo do ItemInfo original do bRO.
# O ItemInfo do cliente usa CRLF. Antes a saída saía em LF por causa do newline="\n" na escrita.

# Traduzir os nomes dos itens (identifiedDisplayName / unidentifiedDisplayName)? O script antigo
# (Google) só traduzia descrições. Com um modelo pequeno, False deixa os nomes originais.

# Expressões regulares nativas
RE_ID_DISPLAY = re.compile(r"^(\s*identifiedDisplayName\s*=\s*\")(.*)(\"\s*,?\s*(?:--.*)?)$")
RE_UNID_DISPLAY = re.compile(r"^(\s*unidentifiedDisplayName\s*=\s*\")(.*)(\"\s*,?\s*(?:--.*)?)$")
RE_UNID_DESC = re.compile(r"^(\s*unidentifiedDescriptionName\s*=\s*\{)(.*)$")

INICIO_DESCRICAO = re.compile(r"^(?P<prefixo>\s*identifiedDescriptionName\s*=\s*\{)(?P<resto>.*)$")
FIM_DESCRICAO = re.compile(r"^\s*\},?\s*(?:--.*)?$")
FECHAMENTO_INLINE = re.compile(r"\}\s*,?\s*(?:--.*)?$")
STRING_LUA = re.compile(r'"((?:\\.|[^"\\])*)"')

RE_COR = re.compile(r"(\^[0-9A-Fa-f]{6})")   # códigos de cor do jogo, ex.: ^990099
# Placeholders semânticos em português, com contador independente por categoria.
# Marcadores literais que já existam no arquivo também são protegidos para evitar colisões.
RE_MARCADOR = re.compile(r"_(COR|TOKEN|QUEBRA)(\d+)")
RE_PROTEGIDO = re.compile(r"(\\n|\^[0-9A-Fa-f]{6}|_(?:COR|TOKEN|QUEBRA)\d+|_{3,}|_\d+)")
RE_LETRAS = re.compile(r"[A-Za-z]{2,}")      # tem palavra para traduzir?
RE_NUMEROS = re.compile(r"\d+")

TEXTO_LUPA = "Item não identificado. Pode ser identificado com uma ^990099[Lupa]^000000."

# Caracteres tipográficos que o latin-1 não tem
TABELA_TIPOGRAFICA = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "..."})

def salvar_cache():
    # Escrita atômica: evita deixar o JSON truncado se a execução for interrompida.
    temporario = CAMINHO_CACHE + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(cache_traducoes, f, ensure_ascii=False, indent=4)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporario, CAMINHO_CACHE)


def aguardar_retomada():
    """Bloqueia a thread de tradução enquanto a interface estiver em pausa."""
    while not evento_pausa.wait(timeout=0.2):
        if evento_stop.is_set():
            raise InterruptedError("Tradução encerrada.")
    if evento_stop.is_set():
        raise InterruptedError("Tradução encerrada.")

def montar_mensagens(texto, is_nome_item, reforcar_marcadores=False, marcadores_forcados=None):
    # --- PROMPTS ---
    instrucao_system = (
        "[SISTEMA: MODO COMPRESSÃO - RESTRIÇÃO GEOMÉTRICA CRÍTICA]\n"
        "TAREFA: Traduzir estritamente do Inglês para o Português (Brasil).\n"
        "PROIBIDO: Adicionar explicações, notas, saudações ou repetir regras. Manter o texto puro.\n"
        "ÂNCORA SINTÁTICA: Identificadores técnicos estruturais presentes no texto de entrada são constantes invariáveis. Preserve-os exatamente, sem traduzir, modificar, duplicar ou inventar identificadores ausentes."
    )
    
    if reforcar_marcadores:
        marcadores_esperados = (
            marcadores_forcados
            if marcadores_forcados is not None
            else [f"_{tipo}{indice}" for tipo, indice in RE_MARCADOR.findall(texto)]
        )
        if marcadores_esperados:
            sequencia_marcadores = ", ".join(marcadores_esperados)
            instrucao_system += (
                f"LINTER ATIVO: A saída OBRIGATORIAMENTE deve conter a sequência exata de controle: [{sequencia_marcadores}]. \n"
                "Se omitir ou alterar a ordem de um único marcador, a compilação quebrará. \n"
                "Trate-os como constantes de código."
            )

    return [
        {
            "role": "system",
            "content": instrucao_system
        },
        {
            "role": "user",
            "content": texto
        }
    ]

def limpar_resposta(texto, original):
    texto = texto.strip()
    texto = re.sub(r"\s*\r?\n\s*", " ", texto)
    aspas = "\"'“”‘’"
    if len(texto) >= 2 and texto[0] in aspas and texto[-1] in aspas:
        texto = texto[1:-1].strip()
    texto = texto.translate(TABELA_TIPOGRAFICA)
    texto = re.sub(r"_(COR|TOKEN|QUEBRA)\s+(\d+)", r"_\1\2", texto, flags=re.IGNORECASE)
    texto = re.sub(r"_(COR|TOKEN|QUEBRA)(\d+)", lambda m: f"_{m.group(1).upper()}{m.group(2)}", texto, flags=re.IGNORECASE)
    if texto.endswith(".") and not original.rstrip().endswith("."):
        texto = texto[:-1]
    return texto


def motivo_recusa(original, traducao):
    """Recusa respostas vazias ou que alterem a sequência dos placeholders."""
    if not traducao:
        return "resposta vazia"
    if RE_MARCADOR.findall(original) != RE_MARCADOR.findall(traducao):
        return "marcadores de estrutura/cor perdidos, alterados ou reordenados, movendo para fallback."
    if "^" in traducao and "^" not in original:
        return "código de cor inventado"
    return None

def traduzir_trecho(texto, is_nome_item, onde, reforcar_marcadores=False, marcadores_forcados=None):
    """
    Traduz e armazena o par mensagem/resposta exatamente como enviado à LLM.
    Se o texto contiver placeholders semânticos (_COR1, _TOKEN1...), ambos permanecem no cache.
    A restauração das patterns acontece somente depois que esta função retorna.
    """
    chave = f"{'N' if is_nome_item else 'D'}|{texto}"

    # A chave contém a mensagem marcada original; o valor contém a tradução marcada.
    if chave in cache_traducoes:
        traducao_marcada = cache_traducoes[chave]
        motivo_cache = motivo_recusa(texto, traducao_marcada)
        if not motivo_cache:
            return traducao_marcada
        print(f"[AVISO] Cache de placeholders inválido descartado ({motivo_cache}): {onde}")
        del cache_traducoes[chave]

    if "(null)" in texto:
        cache_traducoes[chave] = "(null)"
        salvar_cache()
        aguardar_retomada()        
        return "(null)"
    if " " == texto:
        cache_traducoes[chave] = " "
        salvar_cache()
        aguardar_retomada()          
        return " "
        
    aguardar_retomada()

    try:
        response = llm.create_chat_completion(
            messages=montar_mensagens(texto, is_nome_item, reforcar_marcadores, marcadores_forcados),
            temperature=0.0,
            max_tokens=min(3072, len(texto) * 64),
        )
        conteudo_cru = response['choices'][0]['message']['content']
        traducao_marcada = limpar_resposta(conteudo_cru, texto)

        # Remove asteriscos de negrito que o modelo insiste em colocar às vezes.
        traducao_marcada = traducao_marcada.replace("**", "").replace("*", "")
        traducao_marcada = traducao_marcada.replace('"', "'").replace("Traduza para 'pt':", "").replace('\\\"', "'")
    except Exception as e:
        if isinstance(e, InterruptedError) or evento_stop.is_set():
            raise InterruptedError("Tradução interrompida pelo usuário.")
        para_revisar.append((onde, texto, f"erro na LLM: {e}"))
        return texto

    motivo = motivo_recusa(texto, traducao_marcada)
    if motivo:
        detalhe = f"{motivo}: {traducao_marcada!r}"
        para_revisar.append((onde, texto, detalhe))
        print(f"[CACHE NÃO GRAVADO] {onde} — {motivo}")
        marcadores_esperados = " ".join(
            f"_{tipo}{indice}" for tipo, indice in RE_MARCADOR.findall(texto)
        )
        marcadores_recebidos = " ".join(
            f"_{tipo}{indice}" for tipo, indice in RE_MARCADOR.findall(traducao_marcada)
        )
        print(f"  Marcadores esperados: {marcadores_esperados}")
        print(f"  Marcadores recebidos: {marcadores_recebidos}")
        return texto  # mantém a mensagem marcada original; não grava resposta inválida.

    # IMPORTANTE: grava antes de restaurar tags. A chave E o valor ficam com placeholders.
    # Assim, o próximo processamento reutiliza a tradução marcada e só então restaura
    # cores, escapes Lua e separadores conforme a tabela de tags reconstruída no bloco.
    cache_traducoes[chave.replace('\\\"',"'")] = traducao_marcada.replace('\\\"',"'")
    salvar_cache()
    aguardar_retomada()
    return traducao_marcada

def traduzir_linha(texto, is_nome_item, onde, reforcar_marcadores=False):
    """Traduz uma string protegendo cores, escapes Lua e patterns estruturais."""
    esquerda = texto[:len(texto) - len(texto.lstrip())]
    direita = texto[len(texto.rstrip()):]
    texto_limpo = texto.strip()

    match_lista = re.match(r"^(\d+[\.\)\-\s:]+\s*)", texto_limpo)
    prefixo_lista = ""
    if match_lista:
        prefixo_lista = match_lista.group(1)
        texto_limpo = texto_limpo[len(prefixo_lista):].strip()

    if not RE_LETRAS.search(texto_limpo):
        return esquerda + prefixo_lista + texto_limpo + direita

    texto_marcado, tags = extrair_preservando_tags(texto_limpo)
    revisoes_antes = len(para_revisar)
    traducao = traduzir_trecho(
        texto_marcado,
        is_nome_item,
        onde,
        reforcar_marcadores=False,
    )

    marcadores_esperados = RE_MARCADOR.findall(texto_marcado)
    falha_marcadores = (
        RE_MARCADOR.findall(traducao) != marcadores_esperados
        or any(
            motivo.startswith(
                "marcadores de estrutura/cor perdidos, alterados ou reordenados"
            )
            for _, _, motivo in para_revisar[revisoes_antes:]
        )
    )

    # No fallback individual, tenta primeiro com o prompt normal e só reforça
    # a sequência de marcadores se a validação estrutural da primeira tentativa falhar.
    if reforcar_marcadores and falha_marcadores:
        sequencia_marcadores_linha = " ".join(
            f"_{tipo}{indice}" for tipo, indice in marcadores_esperados
        )
        print(
            f"[RETRY] {onde} — nova tentativa individual; "
            f"marcadores da linha: {sequencia_marcadores_linha}"
        )
        revisoes_antes_retry = len(para_revisar)
        traducao_retry = traduzir_trecho(
            texto_marcado,
            is_nome_item,
            f"{onde} (retry)",
            reforcar_marcadores=True,
            marcadores_forcados=[
                f"_{tipo}{indice}" for tipo, indice in marcadores_esperados
            ],
        )
        retry_ok = (
            len(para_revisar) == revisoes_antes_retry
            and RE_MARCADOR.findall(traducao_retry) == marcadores_esperados
        )

        if retry_ok:
            # A falha inicial foi recuperada; não deve aparecer no arquivo de revisão.
            del para_revisar[revisoes_antes:revisoes_antes_retry]
            traducao = traducao_retry
            print(f"[RETRY OK] {onde} — linha recuperada e cacheada.")
        else:
            # Mantém apenas o registro da tentativa definitiva.
            del para_revisar[revisoes_antes:revisoes_antes_retry]
            print(f"[RETRY FALHOU] {onde} — mantendo a linha original.")

    if RE_MARCADOR.findall(texto_marcado) != RE_MARCADOR.findall(traducao):
        return texto

    return esquerda + prefixo_lista + restaurar_tags(traducao, tags) + direita


def proximo_marcador(tags, tipo, texto=""):
    """Gera um identificador semântico ainda não usado nem presente no texto original."""
    usados = {tag["marcador"] for tag in tags}
    indice = 1
    while f"_{tipo}{indice}" in usados or f"_{tipo}{indice}" in texto:
        indice += 1
    return f"_{tipo}{indice}"


def extrair_preservando_tags(texto, tags=None):
    """Protege patterns usando marcadores semânticos em português."""
    if tags is None:
        tags = []

    def _substituir(m):
        valor = m.group(0)
        antes = texto[m.start() - 1] if m.start() > 0 else ""
        depois = texto[m.end()] if m.end() < len(texto) else ""
        espaco_esquerda = bool(antes and antes.isalnum())
        espaco_direita = bool(depois and depois.isalnum())

        if valor.startswith("^"):
            tipo = "COR"
        elif valor == r"\n":
            tipo = "QUEBRA"
        else:
            tipo = "TOKEN"

        marcador = proximo_marcador(tags, tipo, texto)
        tags.append({
            "marcador": marcador,
            "valor": valor,
            "tipo": tipo.lower(),
            "espaco_esquerda": espaco_esquerda,
            "espaco_direita": espaco_direita,
        })
        return f"{' ' if espaco_esquerda else ''}{marcador}{' ' if espaco_direita else ''}"

    return RE_PROTEGIDO.sub(_substituir, texto), tags


def restaurar_tags(texto, tags):
    """Restaura os valores originais e remove apenas os espaços inseridos ao redor dos markers."""
    partes = []
    ultimo = 0
    tags_por_marcador = {tag["marcador"]: tag for tag in tags}

    for m in RE_MARCADOR.finditer(texto):
        marcador = m.group(0)
        tag = tags_por_marcador.get(marcador)
        if tag is None:
            continue

        esquerda = texto[ultimo:m.start()]
        if tag.get("espaco_esquerda") and esquerda.endswith(" "):
            esquerda = esquerda[:-1]
        partes.append(esquerda)
        partes.append(tag["valor"])

        ultimo = m.end()
        if tag.get("espaco_direita") and texto[ultimo:ultimo + 1] == " ":
            ultimo += 1

    partes.append(texto[ultimo:])
    return "".join(partes)


def traduzir_bloco_descricoes(conteudo_linhas, onde):
    """Agrupa strings do array para dar contexto à LLM e depois remonta cada elemento."""
    if not conteudo_linhas:
        return []

    tags = []
    marcadores_separadores = set()
    partes = []
    bordas = []

    for posicao, (_, texto_original) in enumerate(conteudo_linhas):
        esquerda = texto_original[:len(texto_original) - len(texto_original.lstrip())]
        direita = texto_original[len(texto_original.rstrip()):]
        bordas.append((esquerda, direita))

        if posicao:
            # Um marcador _TOKENn representa a vírgula entre elementos do array; ele separa os segmentos.
            # e o escritor mantém a vírgula física original fora das strings Lua.
            marcador_separador = proximo_marcador(tags, "TOKEN", texto_original)
            tags.append({
                "marcador": marcador_separador,
                "valor": ",",
                "tipo": "separador",
                "espaco_esquerda": True,
                "espaco_direita": True,
            })
            marcadores_separadores.add(marcador_separador)
            partes.append(f" {marcador_separador} ")

        texto_marcado, tags = extrair_preservando_tags(texto_original.strip(), tags)
        partes.append(texto_marcado)

    texto_unido = "".join(partes)
    if not RE_LETRAS.search(RE_MARCADOR.sub("", texto_unido)):
        return [texto for _, texto in conteudo_linhas]

    # (null) tem tratamento próprio no tradutor atual; não misture com um bloco maior.
    if any("(null)" in texto for _, texto in conteudo_linhas):
        return [
            traduzir_linha(
                texto,
                False,
                f"{onde}, linha {indice + 1}",
                reforcar_marcadores=True,
            )
            for indice, texto in conteudo_linhas
        ]

    revisoes_antes = len(para_revisar)
    traducao_unida = traduzir_trecho(texto_unido, False, onde)

    marcadores_esperados = RE_MARCADOR.findall(texto_unido)
    marcadores_recebidos = RE_MARCADOR.findall(traducao_unida)
    houve_falha = (
        len(para_revisar) > revisoes_antes
        or marcadores_recebidos != marcadores_esperados
    )

    if houve_falha:
        # traduzir_trecho devolve o original quando rejeita uma resposta.
        # Por isso, a diferença de marcadores também precisa ser identificada
        # pelo motivo registrado em para_revisar.
        falha_marcadores = (
            marcadores_recebidos != marcadores_esperados
            or any(
                motivo.startswith(
                    "marcadores de estrutura/cor perdidos, alterados ou reordenados"
                )
                for _, _, motivo in para_revisar[revisoes_antes:]
            )
        )

        if falha_marcadores:
            print(f"[RETRY] {onde} — nova tentativa após falha de marcadores.")
            revisoes_antes_retry = len(para_revisar)
            traducao_retry = traduzir_trecho(
                texto_unido,
                False,
                f"{onde} (retry)",
                reforcar_marcadores=True,
            )
            marcadores_retry = RE_MARCADOR.findall(traducao_retry)

            retry_ok = (
                len(para_revisar) == revisoes_antes_retry
                and marcadores_retry == marcadores_esperados
            )

            if retry_ok:
                # Remove o registro da primeira tentativa, agora recuperada.
                del para_revisar[revisoes_antes:revisoes_antes_retry]
                traducao_unida = traducao_retry
                marcadores_recebidos = marcadores_retry
                print(f"[RETRY OK] {onde} — bloco recuperado e cacheado.")
            else:
                print(f"[RETRY FALHOU] {onde} — acionando fallback linha a linha.")
                # Registros de tentativas do bloco não representam falhas finais.
                # O fallback registrará somente as linhas que realmente falharem.
                del para_revisar[revisoes_antes:]
                return [
                    traduzir_linha(
                        texto,
                        False,
                        f"{onde}, linha {indice + 1}",
                        reforcar_marcadores=True,
                    )
                    for indice, texto in conteudo_linhas
                ]
        else:
            print(f"[FALLBACK] {onde} — falha não relacionada a marcadores.")
            del para_revisar[revisoes_antes:]
            return [
                traduzir_linha(
                    texto,
                    False,
                    f"{onde}, linha {indice + 1}",
                    reforcar_marcadores=True,
                )
                for indice, texto in conteudo_linhas
            ]

    textos_traduzidos = []
    inicio_trecho = 0
    for marcador in RE_MARCADOR.finditer(traducao_unida):
        if marcador.group(0) in marcadores_separadores:
            trecho = traducao_unida[inicio_trecho:marcador.start()]
            textos_traduzidos.append(restaurar_tags(trecho, tags).strip())
            inicio_trecho = marcador.end()

    trecho_final = traducao_unida[inicio_trecho:]
    textos_traduzidos.append(restaurar_tags(trecho_final, tags).strip())

    if len(textos_traduzidos) != len(conteudo_linhas):
        return [
            traduzir_linha(
                texto,
                False,
                f"{onde}, linha {indice + 1}",
                reforcar_marcadores=True,
            )
            for indice, texto in conteudo_linhas
        ]

    return [
        esquerda + texto + direita
        for texto, (esquerda, direita) in zip(textos_traduzidos, bordas)
    ]


def ler_bloco_descricoes(linhas, indice, correspondencia):
    prefixo = correspondencia.group("prefixo")
    resto = correspondencia.group("resto")
    
    conteudo_linhas = []
    fim_inline = FECHAMENTO_INLINE.search(resto)

    if fim_inline:
        strings = STRING_LUA.findall(resto[:fim_inline.start()])
        if strings:
            conteudo_linhas.append((indice, strings[0]))
        return [linhas[indice]], conteudo_linhas, indice

    bloco = [linhas[indice]]
    strings = STRING_LUA.findall(resto)
    if strings:
        conteudo_linhas.append((indice, strings[0]))

    atual = indice + 1
    while atual < len(linhas) and not FIM_DESCRICAO.match(linhas[atual]):
        bloco.append(linhas[atual])
        strings = STRING_LUA.findall(linhas[atual])
        if strings:
            conteudo_linhas.append((atual, strings[0]))
        atual += 1

    if atual >= len(linhas):
        raise ValueError(f"Bloco identifiedDescriptionName sem fechamento na linha {indice + 1}.")

    bloco.append(linhas[atual])
    return bloco, conteudo_linhas, atual



def escapar_string_lua(texto):
    # Preserva os escapes Lua \n existentes sem duplicar barras invertidas.
    # Guardamos a sequência inteira (inclusive \\n, se houver) e restauramos ao final.
    escapes_lua = []

    def _proteger_escape(m):
        indice = len(escapes_lua)
        escapes_lua.append(m.group(0))
        return f"\x00ITEMINFO_LUA_ESCAPE_{indice}\x00"

    texto = re.sub(r"\\+n", _proteger_escape, texto)
    texto = (
        texto.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
        .replace('\\"', "'")
        .replace('\\\"', "'")
    )

    for indice, escape in enumerate(escapes_lua):
        texto = texto.replace(f"\x00ITEMINFO_LUA_ESCAPE_{indice}\x00", escape)
    return texto
    
def detectar_threads_para_inferencia():
    """
    Detecta o número ideal de threads para inferência LLM.

    - Prefere núcleos físicos (evita oversubscription por SMT, que reduz
      throughput por contenção de memória).
    - Reserva 1 thread para o SO/UI, evitando travamentos no Tkinter
      durante builds que rodam 100% em CPU.
    - Permite override via variável de ambiente TRADUTOR_THREADS, útil
      para CI/workflows que precisam de controle explícito.
    """
    override = os.environ.get("TRADUTOR_THREADS")
    if override:
        try:
            n = int(override)
            if n > 0:
                print(f"[THREADS] Usando override via TRADUTOR_THREADS={n}")
                return n
        except ValueError:
            print(f"[AVISO] TRADUTOR_THREADS inválido ({override!r}); ignorando.")

    try:
        import psutil
        fisicos = psutil.cpu_count(logical=False)
        if fisicos and fisicos > 0:
            threads = max(1, fisicos - 1)
            print(f"[THREADS] Detectados {fisicos} núcleos físicos; usando {threads}.")
            return threads
    except ImportError:
        print("[AVISO] psutil não instalado; detecção cairá no fallback. "
              "Recomendado: pip install psutil")

    # Fallback: os.cpu_count() retorna lógicos em muitas plataformas.
    # Em CPUs sem SMT (Phenom II, i5 antigos) o valor está correto;
    # em CPUs com SMT pode superestimar (oversubscription), mas é
    # preferível a travar com um número fixo.
    logicos = os.cpu_count() or 4
    threads = max(1, logicos - 1)
    print(f"[THREADS] Fallback: {logicos} núcleos lógicos detectados; usando {threads}.")
    return threads
    
def executar_traducao(caminho_entrada, caminho_modelo, fila):
    global CAMINHO_ENTRADA, CAMINHO_SAIDA, CAMINHO_CACHE, CAMINHO_REVISAR
    global NOME_MODELO_GGUF, CAMINHO_ESTADO, cache_traducoes, para_revisar, llm

    CAMINHO_ENTRADA = caminho_entrada
    pasta_saida = os.path.dirname(os.path.abspath(caminho_entrada))
    CAMINHO_SAIDA = os.path.join(pasta_saida, "ItemInfo_traduzido.lua")
    CAMINHO_CACHE = os.path.join(pasta_saida, "cache_traducao_itens.json")
    CAMINHO_REVISAR = os.path.join(pasta_saida, "traducao_revisar.txt")
    CAMINHO_ESTADO = os.path.join(pasta_saida, "progresso_traducao_itens.json")
    NOME_MODELO_GGUF = caminho_modelo
    cache_traducoes = {}
    para_revisar = []

    if os.path.exists(CAMINHO_CACHE):
        try:
            with open(CAMINHO_CACHE, "r", encoding="utf-8") as f:
                cache_traducoes = json.load(f)
        except Exception:
            cache_traducoes = {}
        cache_traducoes = {k: v for k, v in cache_traducoes.items()
                           if isinstance(k, str) and k[:2] in ("N|", "D|")}

    if not os.path.isfile(CAMINHO_ENTRADA):
        raise FileNotFoundError(f"Arquivo não encontrado: {CAMINHO_ENTRADA}")
    if not os.path.isfile(NOME_MODELO_GGUF):
        raise FileNotFoundError(f"Modelo GGUF não encontrado: {NOME_MODELO_GGUF}")

    with open(CAMINHO_ENTRADA, "r", encoding=ENCODING_ARQUIVO) as f:
        conteudo = f.read()
    linhas = conteudo.split("\n")
    if linhas and linhas[-1] == "":
        linhas.pop()

    ids_itens = []
    nomes_originais = {}
    for linha in linhas:
        m = re.match(r"^\s*\[(\d+)\]\s*=\s*\{", linha)
        if m:
            ids_itens.append(m.group(1))
        n = RE_ID_DISPLAY.match(linha)
        if n and ids_itens:
            nomes_originais[ids_itens[-1]] = n.group(2)

    estado_valido = False
    try:
        with open(CAMINHO_ESTADO, "r", encoding="utf-8") as f:
            estado = json.load(f)
        if estado.get("arquivo_entrada") == os.path.abspath(CAMINHO_ENTRADA):
            estado_valido = True
        else:
            estado = {}
    except Exception:
        estado = {}
    concluidos = set(estado.get("itens_concluidos", [])) & set(ids_itens)

    # Corrige estados antigos que marcaram como concluído um item mesmo com
    # falhas definitivas: o relatório de revisão tem precedência sobre o progresso.
    if os.path.isfile(CAMINHO_REVISAR):
        try:
            with open(CAMINHO_REVISAR, "r", encoding="utf-8") as f:
                for registro in f:
                    falha = re.match(r"ID (\d+)\b", registro)
                    if falha:
                        item_com_falha = falha.group(1)
                        if item_com_falha in concluidos:
                            concluidos.discard(item_com_falha)
                            print(f"[RECUPERAÇÃO] ID {item_com_falha} removido dos concluídos por possuir falha registrada.")
        except Exception as erro_revisao:
            print(f"[AVISO] Não foi possível consultar falhas anteriores: {erro_revisao}")

    # Migração única: só infere progresso pelo arquivo de saída quando ainda não
    # existe um estado válido. Depois disso, o JSON é a fonte da verdade.
    linhas_saida_existente = None
    if os.path.isfile(CAMINHO_SAIDA):
        try:
            with open(CAMINHO_SAIDA, "r", encoding=ENCODING_ARQUIVO) as f:
                linhas_saida_existente = f.read().split("\n")
            if linhas_saida_existente and linhas_saida_existente[-1] == "":
                linhas_saida_existente.pop()
            if not estado_valido:
                blocos_entrada = {}
                blocos_saida = {}
                def separar_blocos(linhas_fonte, destino):
                    id_bloco = None
                    buffer = []
                    for linha_fonte in linhas_fonte:
                        match = re.match(r"^\s*\[(\d+)\]\s*=\s*\{", linha_fonte)
                        if match:
                            if id_bloco is not None:
                                destino[id_bloco] = buffer
                            id_bloco = match.group(1)
                            buffer = [linha_fonte]
                        elif id_bloco is not None:
                            buffer.append(linha_fonte)
                    if id_bloco is not None:
                        destino[id_bloco] = buffer
                separar_blocos(linhas, blocos_entrada)
                separar_blocos(linhas_saida_existente, blocos_saida)
                for item_id in ids_itens:
                    if item_id in blocos_saida and item_id in blocos_entrada:
                        if blocos_saida[item_id] != blocos_entrada[item_id]:
                            concluidos.add(item_id)
                print("[INFO] Progresso legado recuperado do arquivo de saída.")
        except Exception as erro_estado:
            linhas_saida_existente = None
            print(f"[AVISO] Não foi possível recuperar progresso do arquivo de saída: {erro_estado}")

    # ETA incremental: média aritmética atualizada a cada item finalizado.
    # O contador de restantes considera apenas itens ainda não processados nesta execução.
    metricas_eta = {
        "total": max(0, len(ids_itens) - len(concluidos)),
        "processados": 0,
        "amostras": 0,
        "media_segundos": None,
        "inicio_item": None,
    }

    def salvar_estado():
        temporario = CAMINHO_ESTADO + ".tmp"
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump({
                "arquivo_entrada": os.path.abspath(CAMINHO_ENTRADA),
                "itens_concluidos": sorted(concluidos, key=lambda x: int(x))
            }, f, ensure_ascii=False, indent=2)
        os.replace(temporario, CAMINHO_ESTADO)

    def notificar(item_id, nome):
        restantes = max(0, metricas_eta["total"] - metricas_eta["processados"])
        media = metricas_eta["media_segundos"]
        eta_segundos = media * restantes if media is not None else None
        fila.put(("progresso", len(concluidos), len(ids_itens), item_id, nome,
                  eta_segundos, restantes))

    def concluir_item(item_id):
        if item_id is None:
            return

        inicio = metricas_eta["inicio_item"]
        if inicio is not None:
            duracao = max(0.0, time.monotonic() - inicio)
            metricas_eta["amostras"] += 1
            n = metricas_eta["amostras"]
            media_anterior = metricas_eta["media_segundos"] or 0.0
            metricas_eta["media_segundos"] = media_anterior + (duracao - media_anterior) / n
            metricas_eta["processados"] += 1
            metricas_eta["inicio_item"] = None
        prefixo_falha = f"ID {item_id} "
        falhou = any(onde.startswith(prefixo_falha) for onde, _, _ in para_revisar)
        if falhou:
            concluidos.discard(item_id)
            print(f"[PENDENTE] ID {item_id} — possui falha(s) definitiva(s); será reprocessado na próxima execução.")
        elif item_id not in concluidos:
            concluidos.add(item_id)
        salvar_estado()
        restantes = max(0, metricas_eta["total"] - metricas_eta["processados"])
        media = metricas_eta["media_segundos"]
        eta_segundos = media * restantes if media is not None else None
        fila.put(("progresso", len(concluidos), len(ids_itens), item_id,
                  "Item concluído" if not falhou else "Item pendente para revisão",
                  eta_segundos, restantes))

    # Se a execução foi pausada, aguarda aqui antes de carregar o modelo.
    aguardar_retomada()

    restantes_iniciais = max(0, metricas_eta["total"] - metricas_eta["processados"])
    fila.put(("progresso", len(concluidos), len(ids_itens), "", "Preparando modelo...", None, restantes_iniciais))
    print("Carregando o modelo LLM Local para tradução de itens...")
    from llama_cpp import Llama
    llm = Llama(
        model_path=NOME_MODELO_GGUF,
        n_ctx=3072,
        n_threads=detectar_threads_para_inferencia(),
        n_gpu_layers=-1,
        verbose=False
    )

    if not os.path.exists(CAMINHO_ENTRADA):
        print(f"Erro: O arquivo '{CAMINHO_ENTRADA}' não foi encontrado.")
        sys.exit(1)

    with open(CAMINHO_ENTRADA, "r", encoding=ENCODING_ARQUIVO) as f:
        conteudo = f.read()

    # Não usar splitlines(): em latin-1 o byte 0x85 vira NEL, que o splitlines trata como quebra de
    # linha e cortaria no meio de um nome coreano.
    linhas = conteudo.split("\n")
    if linhas and linhas[-1] == "":
        linhas.pop()

    print("Processando e traduzindo ItemInfo estruturado (Modo Offline LLM)...")

    # Reaproveita os blocos já concluídos no arquivo de saída. Como o tradutor
    # preserva a estrutura/quantidade de linhas, a substituição é segura quando
    # os dois arquivos têm a mesma quantidade de linhas.
    if linhas_saida_existente is not None and len(linhas_saida_existente) == len(linhas):
        inicio_bloco = None
        id_bloco = None
        for pos, linha_fonte in enumerate(linhas + [""]):
            match = re.match(r"^\s*\[(\d+)\]\s*=\s*\{", linha_fonte) if pos < len(linhas) else None
            if match or pos == len(linhas):
                if id_bloco in concluidos and inicio_bloco is not None:
                    linhas[inicio_bloco:pos] = linhas_saida_existente[inicio_bloco:pos]
                if match:
                    id_bloco = match.group(1)
                    inicio_bloco = pos
                else:
                    id_bloco = None
                    inicio_bloco = None
    elif concluidos:
        print("[AVISO] Saída anterior tem estrutura/quantidade de linhas diferente; não foi possível reaproveitar blocos concluídos.")

    mapeamento_nomes = {}  
    item_atual_id = None
    substituicoes_linha = {} 
    linhas_bloqueadas_escrita = set()

    indice = 0
    while indice < len(linhas):
        aguardar_retomada()
        linha = linhas[indice]
        
        match_id = re.match(r"^\s*\[(\d+)\]\s*=\s*\{", linha)
        if match_id:
            novo_id = match_id.group(1)
            aguardar_retomada()
            if item_atual_id is not None:
                concluir_item(item_atual_id)
            item_atual_id = None
            if novo_id in concluidos:
                # O bloco já está traduzido e foi preservado da saída anterior.
                # Salta diretamente para o próximo ID, sem analisar/traduzir suas linhas.
                proximo = indice + 1
                while proximo < len(linhas) and not re.match(r"^\s*\[(\d+)\]\s*=\s*\{", linhas[proximo]):
                    proximo += 1
                indice = proximo
                continue
            item_atual_id = novo_id
            metricas_eta["inicio_item"] = time.monotonic()
            notificar(item_atual_id, nomes_originais.get(item_atual_id, "Traduzindo item..."))
       
        # 1. Aplica o texto padrão da Lupa
        match_unid_desc = RE_UNID_DESC.match(linha)
        if match_unid_desc:
            indentacao = linha[:len(linha)-len(linha.lstrip())]
            recuo = indentacao + ("\t" if "\t" in indentacao else "    ")
            substituicoes_linha[indice] = (
                f"{match_unid_desc.group(1)}\n"
                f'{recuo}"{escapar_string_lua(TEXTO_LUPA)}",\n'
                f"{indentacao}}},\n"
            )
            if "}" in linha and linha.index("}") > linha.index("{"):
                pass
            else:
                remover_idx = indice + 1
                while remover_idx < len(linhas) and not FIM_DESCRICAO.match(linhas[remover_idx]):
                    linhas_bloqueadas_escrita.add(remover_idx)
                    remover_idx += 1
                if remover_idx < len(linhas):
                    linhas_bloqueadas_escrita.add(remover_idx)
                indice = remover_idx + 1
                continue

        # 2. Traduz o identifiedDisplayName
        match_id_disp = RE_ID_DISPLAY.match(linha)
        if match_id_disp:
            prefixo, nome_original, sufixo = match_id_disp.groups()
            if nome_original.strip() and TRADUZIR_NOMES:
                nomes_originais[item_atual_id] = nome_original
                notificar(item_atual_id, nome_original)
                print(f"ID {item_atual_id}: Traduzindo -> {nome_original}")
                nome_traduzido = traduzir_linha(nome_original, False, f"ID {item_atual_id} nome")
                mapeamento_nomes[item_atual_id] = nome_traduzido
                substituicoes_linha[indice] = {"tipo": "nome_item", "texto": nome_traduzido, "prefixo": prefixo, "sufixo": sufixo}
            indice += 1
            continue

        # 3. Mapeia unidentifiedDisplayName para replicação posterior
        match_unid_disp = RE_UNID_DISPLAY.match(linha)
        if match_unid_disp:
            prefixo, nome_original, sufixo = match_unid_disp.groups()
            substituicoes_linha[indice] = {"tipo": "unid_nome", "prefixo": prefixo, "sufixo": sufixo, "item_id": item_atual_id, "original": nome_original}
            indice += 1
            continue

        # 4. Traduz as linhas do identifiedDescriptionName
        match_desc = INICIO_DESCRICAO.match(linha)
        if match_desc:
            bloco, conteudo_linhas, fim = ler_bloco_descricoes(linhas, indice, match_desc)
            
            if conteudo_linhas:
                textos_traduzidos = traduzir_bloco_descricoes(
                    conteudo_linhas, f"ID {item_atual_id} descrição"
                )
                for (idx_linha, _), texto_traduzido in zip(conteudo_linhas, textos_traduzidos):
                    substituicoes_linha[idx_linha] = {"tipo": "linha_desc", "texto": texto_traduzido}
            
            indice = fim + 1
            continue

        indice += 1

    aguardar_retomada()

    if item_atual_id is not None:
        concluir_item(item_atual_id)

    print("Salvando novas estruturas no arquivo de saída...")
    with open(CAMINHO_SAIDA, "w", encoding=ENCODING_ARQUIVO, newline=QUEBRA_DE_LINHA, errors="replace") as arq_saida:
        for idx, linha in enumerate(linhas):
            if idx in linhas_bloqueadas_escrita:
                continue
                
            if idx in substituicoes_linha:
                acao = substituicoes_linha[idx]
                if isinstance(acao, str):
                    arq_saida.write(acao)
                elif acao["tipo"] == "linha_desc":
                    # lambda: evita que o re.sub interprete as barras invertidas do texto traduzido
                    texto_escapado = escapar_string_lua(acao["texto"])
                    linha_refeita = re.sub(r'"((?:\\.|[^"\\])*)"', lambda _m: f'"{texto_escapado}"', linha, count=1)
                    arq_saida.write(linha_refeita + "\n")
                elif acao["tipo"] == "nome_item":
                    arq_saida.write(f"{acao['prefixo']}{escapar_string_lua(acao['texto'])}{acao['sufixo']}\n")
                elif acao["tipo"] == "unid_nome":
                    nome_replicado = mapeamento_nomes.get(acao["item_id"]) or acao["original"]
                    arq_saida.write(f"{acao['prefixo']}{escapar_string_lua(nome_replicado)}{acao['sufixo']}\n")
            else:
                arq_saida.write(linha + "\n")

    # Conferência: os ResourceName (nomes de sprite) precisam sair idênticos ao original
    def linhas_resource(caminho):
        with open(caminho, "r", encoding=ENCODING_ARQUIVO) as f:
            return [l for l in f.read().split("\n") if re.search(r"ResourceName\s*=", l)]

    res_orig, res_novo = linhas_resource(CAMINHO_ENTRADA), linhas_resource(CAMINHO_SAIDA)
    iguais = sum(a == b for a, b in zip(res_orig, res_novo))
    aviso = "" if len(res_orig) == len(res_novo) else "  <-- ATENÇÃO: quantidade diferente!"
    print(f"ResourceName idênticos ao original: {iguais}/{len(res_orig)}{aviso}")

    if para_revisar:
        with open(CAMINHO_REVISAR, "w", encoding="utf-8") as f:
            for onde, texto, motivo in para_revisar:
                f.write(f"{onde} | {texto} | {motivo}\n")
        print(f"Atenção: {len(para_revisar)} trecho(s) ficaram em inglês. Veja '{CAMINHO_REVISAR}'.")

    print(f"Concluído! O arquivo '{CAMINHO_SAIDA}' foi gerado.")
    fila.put(("concluido", len(concluidos), len(ids_itens), CAMINHO_SAIDA))


def executar_com_tratamento(caminho_entrada, caminho_modelo, fila):
    try:
        executar_traducao(caminho_entrada, caminho_modelo, fila)
    except Exception as erro:
        print(f"\\n[ERRO] Falha durante a tradução: {erro}")
        import traceback
        traceback.print_exc()
        fila.put(("erro", str(erro)))


def formatar_duracao_eta(segundos):
    segundos = max(0, int(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos:02d}m"
    if minutos:
        return f"{minutos}m {segundos:02d}s"
    return f"{segundos}s"


class TradutorGUI(ctk.CTk):
    BG = "#111318"
    PANEL = "#1b1f27"
    MUTED = "#9299a5"
    GREEN = "#24b36b"

    def __init__(self):
        super().__init__()
        self.title("Tradutor ItemInfo LLM(Local) by AoShinHo")
        self.geometry("810x603")
        self.minsize(810, 603)
        self.configure(fg_color=self.BG)
        self.fila = queue.Queue()
        preferencias = carregar_preferencias_gui()
        self.arquivo_iteminfo = preferencias.get("arquivo_iteminfo", "")
        self.arquivo_modelo = preferencias.get("modelo_gguf", "")
        self.executando = False
        self.pausado = False
        self.overrideredirect(False)
        self._montar_interface()
        self.after(100, self._processar_fila)

    def _montar_interface(self):
        corpo = ctk.CTkFrame(self, fg_color="transparent")
        corpo.pack(fill="both", expand=True, padx=18, pady=(27, 18))

        cabecalho = ctk.CTkFrame(corpo, fg_color="transparent")
        cabecalho.pack(fill="x")
        ctk.CTkLabel(cabecalho, text="TRADUÇÃO OFFLINE DE ITEMINFO",
                     font=ctk.CTkFont(size=36, weight="bold"),
                     text_color=self.GREEN).pack(side="left")
        ctk.CTkLabel(cabecalho, text="⚒️", font=ctk.CTkFont(size=63),
                     text_color="#c4c9d2").pack(side="right", padx=(0, 4), pady=(0,0))
        ctk.CTkLabel(corpo, text="Selecione o arquivo Lua e o modelo local para iniciar.",
                     font=ctk.CTkFont(size=18), text_color="#c4c9d2").pack(anchor="w", pady=(5, 20))

        self._linha_selecao(corpo, "Arquivo ItemInfo.lua", "Selecionar arquivo",
                            self._selecionar_iteminfo, "iteminfo")
        self._linha_selecao(corpo, "Modelo de linguagem (.gguf)", "Selecionar modelo",
                            self._selecionar_modelo, "modelo")

        linha_progresso = ctk.CTkFrame(corpo, fg_color="transparent")
        linha_progresso.pack(fill="x", pady=(26, 9))

        ctk.CTkLabel(linha_progresso, text="PROGRESSO DOS ITENS",
             font=ctk.CTkFont(size=27, weight="bold"),
             text_color=self.MUTED).pack(side="left", anchor="s")

        self.start_button = ctk.CTkButton(
            linha_progresso, text="INICIAR \n TRADUÇÃO", width=180, height=63,
            corner_radius=9, fg_color=self.GREEN, hover_color="#198d53",
            font=ctk.CTkFont(size=18, weight="bold"), text_color="black", command=self._iniciar
        )
        self.start_button.pack(side="right", anchor="s")

        self.progress_outer = ctk.CTkFrame(corpo, fg_color="#6E849A", corner_radius=18, height=54)
        self.progress_outer.pack(fill="x")
        self.progress_outer.pack_propagate(False)
        self.progress_fill = ctk.CTkFrame(self.progress_outer, fg_color=self.GREEN,
                                          corner_radius=18, width=1, height=54)
        self.progress_fill.place(x=0, y=0, relheight=1, relwidth=0)
        self.progress_text = ctk.CTkLabel(self.progress_outer, text="0%", corner_radius=9, fg_color="transparent",
                                           font=ctk.CTkFont(size=27, weight="bold"),
                                           text_color="black")
        self.progress_text.place(relx=0.5, rely=0.5, anchor="center")
        self.eta_text = ctk.CTkLabel(
            self.progress_outer, text="ETA calculando... · 0 restantes",
            fg_color="transparent", font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#111318", anchor="e"
        )
        self.eta_text.place(relx=0.985, rely=0.5, anchor="e")
        self.item_text = ctk.CTkLabel(corpo, text="Aguardando seleção...", fg_color="transparent",
                                      font=ctk.CTkFont(size=18), text_color="#e7eaf0")
        self.item_text.pack(anchor="w", pady=(9, 0))

        rodape = ctk.CTkFrame(corpo, fg_color="transparent")
        rodape.pack(fill="x", side="bottom", pady=(18, 0))
        self.status = ctk.CTkLabel(rodape, text="Pronto para iniciar", fg_color="transparent",
                                   text_color=self.MUTED, font=ctk.CTkFont(size=18))
        self.status.pack(side="left")

    def _linha_selecao(self, parent, label, button_text, command, tipo):
        linha = ctk.CTkFrame(parent, width=180, height=63, fg_color=self.PANEL, corner_radius=9)
        linha.pack(fill="x", pady=9)
        coluna = ctk.CTkFrame(linha, fg_color="transparent")
        coluna.pack(side="left", fill="x", expand=True, padx=15, pady=9)
        ctk.CTkLabel(coluna, text=label, text_color="#e5e8ee",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
        caminho_salvo = self.arquivo_iteminfo if tipo == "iteminfo" else self.arquivo_modelo
        valor = ctk.CTkLabel(coluna, text=caminho_salvo or "Nenhum arquivo selecionado",
                             text_color=self.MUTED, font=ctk.CTkFont(size=18),
                             anchor="w")
        valor.pack(fill="x", anchor="w", pady=(3, 0))
        setattr(self, f"{tipo}_label", valor)
        ctk.CTkButton(linha, text=button_text, width=144, height=36, corner_radius=9,
                      fg_color="#303744", hover_color="#414b5b",
                      command=command).pack(side="right", padx=13, pady=12)

    def _selecionar_iteminfo(self):
        inicial = os.path.dirname(self.arquivo_iteminfo) if os.path.isfile(self.arquivo_iteminfo) else os.getcwd()
        nome_inicial = os.path.basename(self.arquivo_iteminfo) if os.path.isfile(self.arquivo_iteminfo) else ""
        caminho = filedialog.askopenfilename(
            title="Selecione o arquivo ItemInfo.lua",
            initialdir=inicial,
            initialfile=nome_inicial,
            filetypes=[("Arquivo Lua", "*.lua"), ("Todos os arquivos", "*.*")]
        )
        if caminho:
            self.arquivo_iteminfo = caminho
            self.iteminfo_label.configure(text=caminho)
            salvar_preferencias_gui(iteminfo=caminho)

    def _selecionar_modelo(self):
        inicial = os.path.dirname(self.arquivo_modelo) if os.path.isfile(self.arquivo_modelo) else os.getcwd()
        nome_inicial = os.path.basename(self.arquivo_modelo) if os.path.isfile(self.arquivo_modelo) else ""
        caminho = filedialog.askopenfilename(
            title="Selecione o modelo GGUF",
            initialdir=inicial,
            initialfile=nome_inicial,
            filetypes=[("Modelos GGUF", "*.gguf"), ("Todos os arquivos", "*.*")]
        )
        if caminho:
            self.arquivo_modelo = caminho
            self.modelo_label.configure(text=caminho)
            salvar_preferencias_gui(modelo=caminho)

    def _iniciar(self):
        if self.executando:
            return
        if not self.arquivo_iteminfo or not os.path.isfile(self.arquivo_iteminfo):
            messagebox.showwarning("Arquivo necessário", "Selecione um arquivo ItemInfo.lua.")
            return
        if not self.arquivo_modelo or not os.path.isfile(self.arquivo_modelo):
            messagebox.showwarning("Modelo necessário", "Selecione um modelo .gguf.")
            return

        # A pasta de saída é a mesma pasta que contém o ItemInfo.lua.
        self.executando = True
        self.pausado = False
        evento_stop.clear()
        evento_pausa.set()
        self.start_button.configure(state="normal", text="PAUSAR", fg_color="#b83b4b",
                                    hover_color="#922d3a", command=self._alternar_pausa)
        self.status.configure(text="Carregando modelo... (acompanhe detalhes no console)")
        worker = threading.Thread(
            target=executar_com_tratamento,
            args=(self.arquivo_iteminfo, self.arquivo_modelo, self.fila),
            daemon=True
        )
        worker.start()

    def _alternar_pausa(self):
        if not self.executando:
            return
        if not self.pausado:
            self.pausado = True
            evento_pausa.clear()
            self.start_button.configure(state="normal", text="RETOMAR", fg_color="#d28b2d",
                                        hover_color="#ad7023", command=self._alternar_pausa)
            self.status.configure(text="Pausando após o trecho atual...")
        else:
            self.pausado = False
            evento_pausa.set()
            self.start_button.configure(state="normal", text="PAUSAR", fg_color="#b83b4b",
                                        hover_color="#922d3a", command=self._alternar_pausa)
            self.status.configure(text="Tradução retomada...")

    def _processar_fila(self):
        try:
            while True:
                evento = self.fila.get_nowait()
                if evento[0] == "progresso":
                    _, feitos, total, item_id, nome, eta_segundos, restantes = evento
                    percentual = (feitos / total) if total else 0
                    eta_formatado = (
                        "calculando..." if eta_segundos is None
                        else formatar_duracao_eta(eta_segundos)
                    )
                    restantes_formatados = f"{restantes:,}".replace(",", ".")
                    self.eta_text.configure(
                        text=f"ETA {eta_formatado} · {restantes_formatados} restantes"
                    )
                    largura = max(0, int(self.progress_outer.winfo_width() * percentual))
                    self.progress_fill.place_configure(relwidth=percentual)
                    self.progress_text.configure(text=f"{percentual:.0%}")
                    if item_id:
                        self.item_text.configure(text=f"ID {item_id} — {nome[:75]}")
                    else:
                        self.item_text.configure(text=nome)
                    if not self.pausado:
                        self.status.configure(text="Tradução em andamento...")
                elif evento[0] == "erro":
                    _, mensagem = evento
                    self.executando = False
                    self.start_button.configure(state="normal", text="TENTAR NOVAMENTE",
                                                fg_color=self.GREEN, hover_color="#198d53",
                                                command=self._iniciar)
                    self.status.configure(text="Erro na tradução — consulte o console")
                    messagebox.showerror("Erro na tradução", mensagem)
                elif evento[0] == "concluido":
                    _, feitos, total, saida = evento
                    self.progress_fill.place_configure(relwidth=1)
                    self.progress_text.configure(text="100%")
                    self.eta_text.configure(text="Concluído · 0 restantes")
                    self.item_text.configure(text="Tradução concluída!")
                    self.status.configure(text=f"Arquivo gerado: {saida}")
                    self.executando = False
                    self.start_button.configure(state="normal", text="INICIAR NOVAMENTE",
                                                fg_color=self.GREEN, hover_color="#198d53",
                                                command=self._iniciar)
                elif evento[0] == "interrompido":
                    _, feitos, total = evento
                    self.executando = False
                    self.pausado = False
                    self.status.configure(text="Execução encerrada.")
                    self.item_text.configure(text="Execução encerrada.")
                    self.start_button.configure(state="normal", text="INICIAR NOVAMENTE",
                                                fg_color=self.GREEN, hover_color="#198d53",
                                                command=self._iniciar)
        except queue.Empty:
            pass
        self.after(100, self._processar_fila)

if __name__ == "__main__":
    app = TradutorGUI()
    app.mainloop()
