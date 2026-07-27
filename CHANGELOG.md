# Histórico de versões

Todas as alterações relevantes do AutoRender Studio são registradas neste
arquivo.

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
