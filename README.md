# Sistema RH ABMT

Aplicação web para o RH e os gestores acompanharem os procedimentos internos, os prazos de pagamento e
fechamento e os processos de admissão e demissão.

## O que o sistema faz

| Módulo | Para que serve |
|---|---|
| **Painel** | Mostra em uma tela tudo o que vence nos próximos 30 dias, com cores para o que está atrasado (vermelho), vence em até 3 dias (laranja) ou em até 7 dias (amarelo). |
| **Prazos e lembretes** | Pagamentos, fechamentos e obrigações (folha, FGTS, eSocial, 13º…). Prazos mensais e anuais: ao concluir, o próximo é criado automaticamente. Avisa quando a data cai no fim de semana. |
| **Admissões e demissões** | Cada processo gera um checklist com prazos calculados a partir da data de início ou do último dia de trabalho. Mostra quem concluiu cada tarefa e quando. |
| **Procedimentos** | Biblioteca dos procedimentos internos do RH, com busca. |
| **Checklists** | O RH edita os modelos de tarefas de admissão e de demissão. |
| **Usuários** | Perfis **RH** (cadastra e edita tudo) e **Gestor** (consulta e marca tarefas como concluídas). |

Os prazos e checklists iniciais são **exemplos**. Confira as datas com a contabilidade e ajuste conforme a
convenção coletiva de cada empresa.

## Como rodar no computador

Requer Python 3.10 ou superior ([python.org](https://www.python.org/downloads/)).

```bash
git clone https://github.com/bcamoteabmt/sistema-rh-abmt.git
cd sistema-rh-abmt
python -m venv .venv
# Windows: .venv\Scripts\activate    |    Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Abra http://127.0.0.1:5000. No primeiro acesso, o sistema pede para criar o usuário administrador do RH.

## Configuração (variáveis de ambiente)

| Variável | Uso |
|---|---|
| `RH_SECRET_KEY` | Chave das sessões. **Obrigatória em produção**; sem ela, todos precisam fazer login de novo a cada reinício. Gere uma com `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `RH_DB_PATH` | Caminho do banco SQLite (padrão: `rh.db` na pasta do projeto). |
| `RH_HOST` / `PORT` | Endereço e porta (padrão `127.0.0.1:5000`). Use `RH_HOST=0.0.0.0` para acessar de outros computadores da rede. |

## Cuidados com dados (LGPD)

- O arquivo `rh.db` contém os dados do sistema. Ele está no `.gitignore` e **não deve ser enviado ao GitHub**.
  Faça backup copiando esse arquivo.
- Não registre dados sensíveis (CPF, informações de saúde, salários). O sistema foi feito para controlar
  tarefas e prazos, não para guardar documentos pessoais.
- Se o repositório for público, qualquer pessoa vê o código (mas não os dados). Recomenda-se deixá-lo privado.
