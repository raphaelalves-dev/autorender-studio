# Histórico de versões

Todas as alterações relevantes do AutoRender Studio são registradas neste
arquivo.

## 1.2.8 — 07/10/2026

- Uma única linha de Fotos com valor `0` ativa a captura de um JPG por segundo de cada vídeo original.
- Nesse modo, as imagens ficam em `fotos/<GoPro>/` (81 a 86) e o nome conserva render, câmera, gravação e segundo.
- O vídeo é decodificado uma vez por GoPro para evitar abrir o FFmpeg a cada foto; pontos normais mantêm o comportamento anterior.

## 1.2.7 — 07/10/2026

- Novas fotos ficam diretamente em uma única pasta `fotos` ao lado do vídeo final.
- O nome do JPG identifica render, GoPro, gravação e segundo para evitar misturar arquivos de gravações diferentes.
- Fotos já geradas em subpastas permanecem onde estão.

## 1.2.6 — 07/10/2026

- Configurações reorganizadas em abas de Caminhos, Fotos, Produção e Atualizações.
- Na aba Fotos, cada ponto de captura tem um campo próprio, prévia dos segundos e botão para adicionar/remover pontos.
- A quantidade de trios é calculada pelas linhas; os valores antigos são preservados ao abrir a nova interface.

## 1.2.5 — 07/10/2026

- Opção nas Configurações para criar fotos a partir dos vídeos originais de cada GoPro.
- Cada segundo central configurado gera três JPGs nos segundos anterior, escolhido e posterior.
- Quantidade de trios por gravação configurável de 1 a 20; nomes 81 a 86 identificam a câmera.
- Fotos ficam na pasta `FOTOS` junto ao vídeo de saída, separadas por render e gravação.
- Trios fora da duração e falhas de extração são registrados no log sem invalidar o render do vídeo.
- Atualização distribuída por ZIP, usando o histórico e a restauração introduzidos na 1.2.4 local.
- Instalador base sem preset, com configuração inicial simples e seleção do `.MOV` pelo usuário na primeira abertura; caminhos externos de preset permanecem salvos após reiniciar.

## 1.2.4 — 22/07/2026

### Interface

- substituição da faixa de caminhos por cartões independentes e responsivos;
- cartão dedicado à pasta de entrada e ao seu estado de acesso;
- cartão dedicado à pasta de saída e à disponibilidade do servidor;
- atualização automática dos caminhos conforme a configuração diária.

### Servidor

- verificação de acesso executada em segundo plano;
- indicação visual de servidor disponível;
- alerta objetivo quando o destino está offline ou inacessível;
- verificação isolada do estado da renderização.

### Desempenho e compatibilidade

- apenas uma verificação de acesso ativa por vez;
- tarefa leve para evitar bloqueios da interface;
- preservação das configurações de render, preset e qualidade;
- manutenção do redimensionamento e da geometria salva da janela.
