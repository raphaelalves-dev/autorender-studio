# AutoRender Studio v1.2.4

Sistema de renderização automática de vídeos com presets personalizados.

Esta é a versão de código-fonte preparada para o GitHub. Arquivos locais, vídeos,
presets, executáveis do FFmpeg, builds, instaladores, logs e configurações da
máquina não fazem parte do repositório.

## Estrutura do Projeto

```
AutoRenderStudio/
├── backend/           # Código fonte Python (lógica de renderização)
├── frontend/          # Interface gráfica
├── config/            # Arquivos de configuração
├── bin/               # Coloque FFmpeg e FFprobe localmente
├── preset/            # Coloque os presets de vídeo localmente
├── entrada/           # Pasta para vídeos de entrada
├── saida/             # Pasta para vídeos renderizados
├── logs/              # Logs de execução
├── processados/       # Vídeos processados
├── erros/             # Vídeos com erro
├── .venv/             # Ambiente virtual Python
└── AutoRenderPreset_GUI.py  # Arquivo principal
```

## Arquivos de Build e Teste

### 1. INSTALAR.bat
**Descrição:** Instala as dependências necessárias
- Cria ambiente virtual Python (.venv)
- Instala watchdog e pyinstaller
- Prepara o ambiente para desenvolvimento

**Como usar:**
```
INSTALAR.bat
```

### 2. TESTAR_SEM_HISTORICO.bat
**Descrição:** Executa o aplicativo em modo teste sem salvar histórico
- Ativa o ambiente virtual
- Verifica FFmpeg
- Executa a interface gráfica

**Como usar:**
```
TESTAR_SEM_HISTORICO.bat
```

### 3. BUILD_EXE_PORTABLE.bat
**Descrição:** Cria executável portátil (.exe)
- Usa PyInstaller para criar o executável
- Gera pasta dist/AutoRenderPreset/ com todos os arquivos
- Inclui todas as dependências necessárias

**Como usar:**
```
BUILD_EXE_PORTABLE.bat
```
**Saída:** `dist/AutoRenderPreset/AutoRenderPreset.exe`

### 4. BUILD_INSTALLER_COM_SENHA.bat
**Descrição:** Cria o EXE portátil e depois o instalador com senha usando Inno Setup
- Requer Inno Setup instalado
- Executa o build do PyInstaller automaticamente
- Pede a senha do instalador na hora
- Gera instalador protegido por senha
- Coloca o instalador na pasta output/
- Aceita Inno Setup 7, 6 ou 5

**Como usar:**
```
BUILD_INSTALLER_COM_SENHA.bat
```
**Saída:** `output/AutoRenderStudio_Setup_1.2.4.exe` e, se necessário por tamanho, arquivos `.bin` junto.

### 5. AutoRenderStudio_Setup.iss
**Descrição:** Script de configuração do Inno Setup
- Define estrutura do instalador
- Configura senha de proteção
- Especifica arquivos a incluir

## Fluxo de Trabalho Recomendado

### Para Desenvolvimento/Teste:
1. Execute `INSTALAR.bat` (primeira vez)
2. Execute `TESTAR_SEM_HISTORICO.bat` para testar

### Para Criar Versão Portátil:
1. Execute `BUILD_EXE_PORTABLE.bat`
2. A pasta `dist/AutoRenderPreset/` conterá o executável e todos os arquivos
3. Distribua toda a pasta

### Para Criar Instalador:
1. Instale Inno Setup: https://jrsoftware.org/isinfo.php
2. Execute `BUILD_INSTALLER_COM_SENHA.bat`
3. Digite a senha desejada quando o script pedir
4. Envie todos os arquivos `AutoRenderStudio_Setup_*` gerados em `output/`

### Checagem antes de produção:
```
CHECK_PROD.bat
```

Essa checagem valida sintaxe, FFmpeg, FFprobe, encoder NVIDIA/AMD/CPU, presets e Inno Setup sem recriar o pacote pesado.

## Requisitos

- Python 3.11 ou superior
- FFmpeg e FFprobe adicionados localmente em `bin/`
- Presets adicionados localmente em `preset/`
- Windows 10 ou superior
- Inno Setup 7 ou 6 (apenas para criar instalador)

## Após baixar ou clonar

1. Execute `INSTALAR.bat`.
2. Coloque `ffmpeg.exe` e `ffprobe.exe` em `bin/`.
3. Coloque os presets `.mov` em `preset/`.
4. Execute `TESTAR_SEM_HISTORICO.bat`.

Na primeira execução, o aplicativo cria `config/settings.json` com caminhos
adequados à máquina local. Esse arquivo não é enviado ao GitHub.

## Dependências Python

```
watchdog>=4.0.0
pyinstaller>=6.0.0
```

## Pastas Importantes

- **bin/**: Contém FFmpeg - OBRIGATÓRIO para renderização
- **preset/**: Contém presets de vídeo - OBRIGATÓRIO para funcionamento
- **entrada/**: Coloque os vídeos aqui para processar
- **saida/**: Vídeos renderizados aparecerão aqui
- **logs/**: Histórico de execução e erros

## Formatos de renderização

- **1 vídeo (82)**: renderiza somente a câmera 82 com o preset individual.
- **2 vídeos (81+82)**: combina o par 81/82 com o preset duplo.
- **Ambos**: gera a composição 81/82 e também a saída individual da câmera 82.
- **Todos (81 a 86)**: renderiza individualmente todos os vídeos presentes no lote com o “Preset Individual (82 / Todos)”; um lote com 81, 82, 83, 84, 85 e 86 gera seis saídas.

Quando a pasta `preset` contém somente um arquivo `.mov`, o app ativa automaticamente o comportamento “Todos”: aplica esse único preset individualmente a cada vídeo da pasta. Com dois presets, os formatos de 81+82 continuam funcionando como configurados.

## Observações

- O modo teste (TESTAR_SEM_HISTORICO.bat) não salva histórico de renderizações
- Certifique-se de que FFmpeg está em bin/
- Certifique-se de que os presets estão em preset/
- Para performance, deixe o codec em `auto`: o app tenta NVIDIA (`h264_nvenc`), AMD (`h264_amf`) e usa CPU (`libx264`) como fallback.
- Em PC com AMD, selecione `h264_amf` se quiser exigir a placa AMD.
- O instalador gerado requer a senha digitada durante `BUILD_INSTALLER_COM_SENHA.bat`
- O histórico é salvo de forma atômica para reduzir risco de JSON corrompido.
- O render tem timeout configurável em `config/settings.json` (`ffmpeg_timeout_seconds`) e limpa saída parcial quando falha.
- A checagem de arquivo estável tem limite configurável (`stable_check_max_wait_seconds`) para não travar em cópias incompletas.
- Os logs principais têm rotação automática para não crescerem sem limite.

## Tamanho do Projeto

- Repositório de código-fonte: menos de 1MB
- FFmpeg, presets, vídeos e builds permanecem fora do GitHub

## Suporte

Para problemas ou dúvidas, verifique os logs em `logs/autorender.log`
