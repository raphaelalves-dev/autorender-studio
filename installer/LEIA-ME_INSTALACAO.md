# AutoRender Studio 1.2.8 — instalação inicial

Este instalador contém o programa, FFmpeg e FFprobe. O preset `.MOV` é fornecido pelo usuário e não vem no pacote.

Na primeira abertura, a janela **Configurações** aparece para selecionar o preset e ajustar as pastas de entrada e saída. Selecione **Preset Individual (82 / Todos)** para o formato Todos (81 a 86). Se usar o formato de duas câmeras, selecione também **Preset Auto**. Clique em **Salvar configurações** antes de iniciar a renderização.

O preset pode ficar na pasta `preset` do programa ou em outra pasta acessível; o caminho escolhido nas Configurações é preservado nas próximas aberturas. Mantenha o arquivo disponível no mesmo caminho.

A versão 1.2.8 já inclui o histórico de instalação e a opção de gerar fotos dos vídeos originais das GoPros. As fotos ficam desligadas inicialmente e podem ser ativadas na aba **Fotos**. Pontos normais geram trios diretamente em `fotos`; uma única linha com `0` gera uma foto por segundo da gravação inteira, em `fotos/<GoPro>`. As próximas versões podem ser aplicadas por **Configurações > Atualizações**; antes de uma atualização, o programa registra a versão instalada para permitir voltar a ela.

## Reinstalar sobre uma instalação existente

Feche o AutoRender antes de executar o instalador. Para uma instalação anterior feita com o instalador, o assistente usa a mesma pasta por padrão. Para uma cópia portátil, escolha manualmente a pasta que contém `AutoRenderPreset.exe`. O instalador mantém `config/settings.json`, presets existentes e pastas de vídeos/logs. As opções e caminhos salvos, inclusive os que apontam para outra unidade ou servidor, continuam válidos. Confira a pasta de destino indicada pelo assistente antes de concluir.
