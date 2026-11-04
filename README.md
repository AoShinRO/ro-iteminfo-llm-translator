# ⚒️ ro-iteminfo-llm-translator

Tradutor offline do `ItemInfo.lua` do Ragnarok Online (inglês → português brasileiro) usando um modelo de linguagem local no formato GGUF, via `llama-cpp-python`. Possui interface gráfica (customtkinter) com barra de progresso, pausa/retomada, cache de traduções e recuperação de progresso entre execuções.

<img width="405" height="327" alt="image" src="https://github.com/user-attachments/assets/b8d00d75-03ab-462b-93c1-258bdb96bd0a" />

## Funcionalidades

- Tradução 100% offline — nenhuma API externa é usada
- Interface gráfica para selecionar o `ItemInfo.lua` e o modelo `.gguf`
- Traduz `identifiedDisplayName`, `unidentifiedDisplayName` e `identifiedDescriptionName`
- Substitui `unidentifiedDescriptionName` pelo texto padrão da Lupa em PT-BR
- Preserva códigos de cor do jogo (`^RRGGBB`), escapes Lua (`\n`) e estrutura do arquivo
- Cache de traduções (`cache_traducao_itens.json`) — trechos repetidos não são retraduzidos
- Arquivo de progresso (`progresso_traducao_itens.json`) — permite interromper e continuar depois
- Lista de trechos que falharam em `traducao_revisar.txt` para revisão manual
- Preserva byte a byte os `ResourceName` (nomes de sprite coreanos) usando encoding `latin-1`
- Saída em CRLF, igual ao ItemInfo original do cliente

## Requisitos

- Python 3.x
- Um modelo GGUF de tradução (veja [modelos sugeridos](https://huggingface.co/models?pipeline_tag=translation&language=en,pt&sort=likes&search=gguf))

## Instalação

### CPU com AVX (mais comum)

```bash
pip install llama-cpp-python customtkinter
```

## Rodando o Modelo na Placa de Vídeo — processadores sem AVX

### GPU AMD (Vulkan)

Baixe o [Vulkan SDK](https://vulkan.lunarg.com/sdk/home#windows) e execute no `cmd`:

```bat
set "VULKAN_SDK=C:/VulkanSDK/1.4.363.0"
set "CMAKE_ARGS=-DGGML_VULKAN=ON -DGGML_NATIVE=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF -DVulkan_INCLUDE_DIR=%VULKAN_SDK%/Include -DVulkan_LIBRARY=%VULKAN_SDK%/Lib/vulkan-1.lib"
set "FORCE_CMAKE=1"
pip install llama-cpp-python --force-reinstall --no-cache-dir --no-binary llama-cpp-python --no-deps

set "NPY_DISABLE_CPU_FEATURES=AVX,AVX2,FMA3,AVX512F"
pip install numpy --no-binary numpy --force-reinstall --config-settings=setup-args="-Dcpu-baseline=none" --config-settings=setup-args="-Dcpu-dispatch=none"
pip install customtkinter
```

### GPU NVIDIA (CUDA)

```bat
set "CMAKE_ARGS=-DGGML_CUDA=ON -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF -DGGML_NATIVE=OFF"
set "FORCE_CMAKE=1"
pip install llama-cpp-python --force-reinstall --no-cache-dir --no-binary llama-cpp-python
```

## Uso

```bat
python ro-iteminfo-llm-translator.py
```

1. Selecione o arquivo `ItemInfo.lua`
2. Selecione o modelo `.gguf`
3. Clique em **INICIAR TRADUÇÃO** — o botão vira **PAUSAR** durante a execução

Os arquivos são gerados na mesma pasta do `ItemInfo.lua` de entrada:

| Arquivo | Descrição |
|---|---|
| `ItemInfo_traduzido.lua` | Arquivo final traduzido |
| `cache_traducao_itens.json` | Cache de traduções (reutilizado entre execuções) |
| `progresso_traducao_itens.json` | Progresso por item (permite retomar) |
| `traducao_revisar.txt` | Trechos que ficaram em inglês para revisão manual |

As últimas seleções de arquivo/modelo ficam salvas em `.tradutor_iteminfo_ultima_selecao.json`.

## Notas técnicas

- O arquivo é lido/escrito em `latin-1` para preservar byte a byte os nomes de sprite coreanos misturados com texto cp1252 — não use detector de encoding
- Códigos de cor, escapes e separadores são substituídos por marcadores (`_COR1`, `_TOKEN1`, `_QUEBRA1`) antes de ir à LLM e restaurados depois; respostas que perdem/reordenam marcadores são rejeitadas com retry e fallback linha a linha
- `TRADUZIR_NOMES = True` no topo do script controla se os nomes dos itens são traduzidos (modelos pequenos podem se sair melhor com `False`)
- Modelo carregado com `n_ctx=3072`, `n_threads=6`, `n_gpu_layers=-1` (todas as camadas na GPU quando disponível)

## Licença

Veja [LICENSE](LICENSE).
