"""Sistema RH ABMT — procedimentos internos, prazos e processos de admissão/demissão."""
import calendar
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("RH_DB_PATH", os.path.join(BASE_DIR, "rh.db"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("RH_SECRET_KEY") or secrets.token_hex(32)

CATEGORIAS_PRAZO = ["Pagamento", "Fechamento", "Obrigação legal", "Benefícios", "Outro"]
RECORRENCIAS = {"nenhuma": "Não repete", "mensal": "Todo mês", "anual": "Todo ano"}
TIPOS_PROCESSO = {"admissao": "Admissão", "demissao": "Demissão"}
PAPEIS = {"rh": "RH", "gestor": "Gestor"}
DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    papel TEXT NOT NULL CHECK (papel IN ('rh', 'gestor')),
    ativo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS prazos (
    id INTEGER PRIMARY KEY,
    titulo TEXT NOT NULL,
    categoria TEXT NOT NULL,
    vencimento TEXT NOT NULL,
    recorrencia TEXT NOT NULL DEFAULT 'nenhuma',
    responsavel TEXT,
    observacoes TEXT,
    concluido_em TEXT
);
CREATE TABLE IF NOT EXISTS procedimentos (
    id INTEGER PRIMARY KEY,
    titulo TEXT NOT NULL,
    categoria TEXT NOT NULL,
    conteudo TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    atualizado_por TEXT
);
CREATE TABLE IF NOT EXISTS modelo_itens (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL CHECK (tipo IN ('admissao', 'demissao')),
    ordem INTEGER NOT NULL,
    descricao TEXT NOT NULL,
    dias_relativos INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS processos (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL CHECK (tipo IN ('admissao', 'demissao')),
    colaborador TEXT NOT NULL,
    cargo TEXT,
    gestor TEXT,
    data_referencia TEXT NOT NULL,
    observacoes TEXT,
    criado_em TEXT NOT NULL,
    encerrado_em TEXT
);
CREATE TABLE IF NOT EXISTS processo_itens (
    id INTEGER PRIMARY KEY,
    processo_id INTEGER NOT NULL REFERENCES processos(id) ON DELETE CASCADE,
    ordem INTEGER NOT NULL,
    descricao TEXT NOT NULL,
    prazo TEXT NOT NULL,
    concluido_em TEXT,
    concluido_por TEXT
);
"""

# Exemplos iniciais — ajuste conforme a convenção coletiva e a orientação da contabilidade.
PRAZOS_INICIAIS = [
    ("Pagamento de salários (até o 5º dia útil)", "Pagamento", 5, "mensal"),
    ("Envio dos eventos periódicos do eSocial", "Obrigação legal", 15, "mensal"),
    ("Recolhimento do FGTS (FGTS Digital)", "Obrigação legal", 20, "mensal"),
    ("Recolhimento de INSS/IRRF (DCTFWeb)", "Obrigação legal", 20, "mensal"),
    ("Fechamento do ponto e envio de variáveis da folha", "Fechamento", 25, "mensal"),
]
PRAZOS_ANUAIS = [
    ("13º salário — 1ª parcela", "Pagamento", 11, 30),
    ("13º salário — 2ª parcela", "Pagamento", 12, 20),
]

MODELOS_INICIAIS = {
    "admissao": [
        ("Receber documentação pessoal do candidato", -7),
        ("Agendar e realizar exame admissional (ASO)", -5),
        ("Preparar contrato de trabalho e acordos", -3),
        ("Enviar evento S-2200 ao eSocial (antes do início)", -1),
        ("Solicitar acessos, e-mail e equipamentos", -1),
        ("Cadastrar benefícios (VT, VR, plano de saúde)", 0),
        ("Integração/boas-vindas com o gestor", 0),
        ("Avaliação do período de experiência (45 dias)", 44),
        ("Avaliação final do período de experiência (90 dias)", 89),
    ],
    "demissao": [
        ("Formalizar aviso (prévio trabalhado ou indenizado)", 0),
        ("Agendar exame demissional (ASO)", 3),
        ("Recolher equipamentos, crachá e revogar acessos", 0),
        ("Calcular verbas rescisórias", 5),
        ("Pagar verbas rescisórias (até 10 dias do término)", 10),
        ("Enviar evento S-2299 ao eSocial", 10),
        ("Entregar documentos (TRCT, guias do seguro-desemprego, FGTS)", 10),
        ("Cancelar benefícios", 10),
    ],
}

PROCEDIMENTO_EXEMPLO = (
    "Fechamento mensal da folha",
    "Folha de pagamento",
    "1. Até o dia 25: gestores enviam ao RH horas extras, faltas e atestados.\n"
    "2. RH confere o ponto e lança as variáveis.\n"
    "3. Enviar prévia da folha para a contabilidade conferir.\n"
    "4. Aprovar a folha e programar os pagamentos.\n"
    "5. Arquivar os holerites assinados.",
)


# ---------------------------------------------------------------- banco de dados

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM prazos").fetchone()[0] == 0:
        hoje = date.today()
        for titulo, categoria, dia, rec in PRAZOS_INICIAIS:
            venc = dia_no_mes(hoje.year, hoje.month, dia)
            if venc < hoje:
                venc = somar_meses(venc, 1, dia)
            db.execute(
                "INSERT INTO prazos (titulo, categoria, vencimento, recorrencia, responsavel) "
                "VALUES (?, ?, ?, ?, 'RH')", (titulo, categoria, venc.isoformat(), rec))
        for titulo, categoria, mes, dia in PRAZOS_ANUAIS:
            venc = date(hoje.year, mes, dia)
            if venc < hoje:
                venc = date(hoje.year + 1, mes, dia)
            db.execute(
                "INSERT INTO prazos (titulo, categoria, vencimento, recorrencia, responsavel) "
                "VALUES (?, ?, ?, 'anual', 'RH')", (titulo, categoria, venc.isoformat()))
    if db.execute("SELECT COUNT(*) FROM modelo_itens").fetchone()[0] == 0:
        for tipo, itens in MODELOS_INICIAIS.items():
            for ordem, (descricao, dias) in enumerate(itens):
                db.execute(
                    "INSERT INTO modelo_itens (tipo, ordem, descricao, dias_relativos) VALUES (?, ?, ?, ?)",
                    (tipo, ordem, descricao, dias))
    if db.execute("SELECT COUNT(*) FROM procedimentos").fetchone()[0] == 0:
        db.execute(
            "INSERT INTO procedimentos (titulo, categoria, conteudo, atualizado_em, atualizado_por) "
            "VALUES (?, ?, ?, ?, 'Sistema')", (*PROCEDIMENTO_EXEMPLO, agora()))
    db.commit()
    db.close()


# ---------------------------------------------------------------- datas

def agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def dia_no_mes(ano, mes, dia):
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def somar_meses(d, meses, dia_original=None):
    total = d.month - 1 + meses
    return dia_no_mes(d.year + total // 12, total % 12 + 1, dia_original or d.day)


def proxima_ocorrencia(vencimento, recorrencia):
    if recorrencia == "mensal":
        return somar_meses(vencimento, 1)
    if recorrencia == "anual":
        return somar_meses(vencimento, 12)
    return None


def parse_data(texto):
    try:
        return date.fromisoformat(texto)
    except (TypeError, ValueError):
        return None


@app.template_filter("data_br")
def data_br(valor):
    d = parse_data(valor) if isinstance(valor, str) else valor
    if not d:
        return valor or ""
    return f"{d.strftime('%d/%m/%Y')} ({DIAS_SEMANA[d.weekday()]})"


@app.template_filter("situacao")
def situacao(valor):
    """Classe CSS conforme a proximidade do prazo."""
    d = parse_data(valor)
    if not d:
        return ""
    dias = (d - date.today()).days
    if dias < 0:
        return "atrasado"
    if dias <= 3:
        return "urgente"
    if dias <= 7:
        return "proximo"
    return ""


@app.template_filter("fim_de_semana")
def fim_de_semana(valor):
    d = parse_data(valor)
    return bool(d and d.weekday() >= 5)


# ---------------------------------------------------------------- autenticação e segurança

@app.before_request
def carregar_usuario():
    g.usuario = None
    if "usuario_id" in session:
        g.usuario = get_db().execute(
            "SELECT * FROM usuarios WHERE id = ? AND ativo = 1", (session["usuario_id"],)).fetchone()
    if request.method == "POST":
        token = session.get("csrf")
        if not token or token != request.form.get("csrf"):
            abort(400)


@app.context_processor
def contexto_global():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf": session["csrf"], "usuario": g.get("usuario"), "PAPEIS": PAPEIS,
            "TIPOS_PROCESSO": TIPOS_PROCESSO, "RECORRENCIAS": RECORRENCIAS,
            "CATEGORIAS_PRAZO": CATEGORIAS_PRAZO}


def login_obrigatorio(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.usuario is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def somente_rh(view):
    @wraps(view)
    @login_obrigatorio
    def wrapper(*args, **kwargs):
        if g.usuario["papel"] != "rh":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


@app.route("/configuracao", methods=["GET", "POST"])
def configuracao_inicial():
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] > 0:
        return redirect(url_for("login"))
    if request.method == "POST":
        nome, email, senha = (request.form.get(k, "").strip() for k in ("nome", "email", "senha"))
        if not nome or not email or len(senha) < 8:
            flash("Preencha todos os campos. A senha deve ter pelo menos 8 caracteres.", "erro")
        else:
            db.execute("INSERT INTO usuarios (nome, email, senha_hash, papel) VALUES (?, ?, ?, 'rh')",
                       (nome, email.lower(), generate_password_hash(senha)))
            db.commit()
            flash("Administrador criado. Faça login.", "ok")
            return redirect(url_for("login"))
    return render_template("configuracao.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
        return redirect(url_for("configuracao_inicial"))
    if request.method == "POST":
        user = db.execute("SELECT * FROM usuarios WHERE email = ? AND ativo = 1",
                          (request.form.get("email", "").strip().lower(),)).fetchone()
        if user and check_password_hash(user["senha_hash"], request.form.get("senha", "")):
            session.clear()
            session["usuario_id"] = user["id"]
            return redirect(url_for("painel"))
        flash("E-mail ou senha inválidos.", "erro")
    return render_template("login.html")


@app.route("/sair", methods=["POST"])
def sair():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- painel

@app.route("/")
@login_obrigatorio
def painel():
    db = get_db()
    limite = (date.today() + timedelta(days=30)).isoformat()
    prazos = db.execute(
        "SELECT * FROM prazos WHERE concluido_em IS NULL AND vencimento <= ? ORDER BY vencimento",
        (limite,)).fetchall()
    itens = db.execute(
        "SELECT i.*, p.colaborador, p.tipo FROM processo_itens i JOIN processos p ON p.id = i.processo_id "
        "WHERE i.concluido_em IS NULL AND p.encerrado_em IS NULL AND i.prazo <= ? ORDER BY i.prazo",
        (limite,)).fetchall()
    processos = listar_processos(abertos=True)
    return render_template("painel.html", prazos=prazos, itens=itens, processos=processos)


# ---------------------------------------------------------------- prazos e lembretes

@app.route("/prazos")
@login_obrigatorio
def prazos():
    db = get_db()
    pendentes = db.execute(
        "SELECT * FROM prazos WHERE concluido_em IS NULL ORDER BY vencimento").fetchall()
    concluidos = db.execute(
        "SELECT * FROM prazos WHERE concluido_em IS NOT NULL ORDER BY concluido_em DESC LIMIT 20").fetchall()
    return render_template("prazos.html", pendentes=pendentes, concluidos=concluidos)


@app.route("/prazos/novo", methods=["GET", "POST"])
@app.route("/prazos/<int:prazo_id>/editar", methods=["GET", "POST"])
@somente_rh
def prazo_form(prazo_id=None):
    db = get_db()
    prazo = None
    if prazo_id:
        prazo = db.execute("SELECT * FROM prazos WHERE id = ?", (prazo_id,)).fetchone() or abort(404)
    if request.method == "POST":
        f = request.form
        dados = (f.get("titulo", "").strip(), f.get("categoria"), f.get("vencimento"),
                 f.get("recorrencia"), f.get("responsavel", "").strip(), f.get("observacoes", "").strip())
        if not dados[0] or not parse_data(dados[2]) or dados[1] not in CATEGORIAS_PRAZO \
                or dados[3] not in RECORRENCIAS:
            flash("Preencha título, categoria, data e recorrência.", "erro")
        else:
            if prazo:
                db.execute("UPDATE prazos SET titulo=?, categoria=?, vencimento=?, recorrencia=?, "
                           "responsavel=?, observacoes=? WHERE id=?", (*dados, prazo_id))
            else:
                db.execute("INSERT INTO prazos (titulo, categoria, vencimento, recorrencia, responsavel, "
                           "observacoes) VALUES (?, ?, ?, ?, ?, ?)", dados)
            db.commit()
            flash("Prazo salvo.", "ok")
            return redirect(url_for("prazos"))
    return render_template("prazo_form.html", prazo=prazo)


@app.route("/prazos/<int:prazo_id>/concluir", methods=["POST"])
@login_obrigatorio
def prazo_concluir(prazo_id):
    db = get_db()
    prazo = db.execute("SELECT * FROM prazos WHERE id = ?", (prazo_id,)).fetchone() or abort(404)
    db.execute("UPDATE prazos SET concluido_em = ? WHERE id = ?", (agora(), prazo_id))
    proxima = proxima_ocorrencia(parse_data(prazo["vencimento"]), prazo["recorrencia"])
    if proxima:
        db.execute("INSERT INTO prazos (titulo, categoria, vencimento, recorrencia, responsavel, observacoes) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (prazo["titulo"], prazo["categoria"], proxima.isoformat(),
                                                 prazo["recorrencia"], prazo["responsavel"], prazo["observacoes"]))
        flash(f"Concluído. Próxima ocorrência criada para {data_br(proxima)}.", "ok")
    else:
        flash("Prazo concluído.", "ok")
    db.commit()
    return redirect(request.form.get("voltar") or url_for("prazos"))


@app.route("/prazos/<int:prazo_id>/excluir", methods=["POST"])
@somente_rh
def prazo_excluir(prazo_id):
    db = get_db()
    db.execute("DELETE FROM prazos WHERE id = ?", (prazo_id,))
    db.commit()
    flash("Prazo excluído.", "ok")
    return redirect(url_for("prazos"))


# ---------------------------------------------------------------- procedimentos internos

@app.route("/procedimentos")
@login_obrigatorio
def procedimentos():
    busca = request.args.get("q", "").strip()
    sql = "SELECT * FROM procedimentos"
    params = ()
    if busca:
        sql += " WHERE titulo LIKE ? OR conteudo LIKE ? OR categoria LIKE ?"
        params = (f"%{busca}%",) * 3
    lista = get_db().execute(sql + " ORDER BY categoria, titulo", params).fetchall()
    return render_template("procedimentos.html", procedimentos=lista, busca=busca)


@app.route("/procedimentos/<int:proc_id>")
@login_obrigatorio
def procedimento(proc_id):
    p = get_db().execute("SELECT * FROM procedimentos WHERE id = ?", (proc_id,)).fetchone() or abort(404)
    return render_template("procedimento.html", p=p)


@app.route("/procedimentos/novo", methods=["GET", "POST"])
@app.route("/procedimentos/<int:proc_id>/editar", methods=["GET", "POST"])
@somente_rh
def procedimento_form(proc_id=None):
    db = get_db()
    p = None
    if proc_id:
        p = db.execute("SELECT * FROM procedimentos WHERE id = ?", (proc_id,)).fetchone() or abort(404)
    if request.method == "POST":
        titulo, categoria, conteudo = (request.form.get(k, "").strip() for k in ("titulo", "categoria", "conteudo"))
        if not titulo or not categoria or not conteudo:
            flash("Preencha título, categoria e conteúdo.", "erro")
        else:
            if p:
                db.execute("UPDATE procedimentos SET titulo=?, categoria=?, conteudo=?, atualizado_em=?, "
                           "atualizado_por=? WHERE id=?",
                           (titulo, categoria, conteudo, agora(), g.usuario["nome"], proc_id))
            else:
                cur = db.execute("INSERT INTO procedimentos (titulo, categoria, conteudo, atualizado_em, "
                                 "atualizado_por) VALUES (?, ?, ?, ?, ?)",
                                 (titulo, categoria, conteudo, agora(), g.usuario["nome"]))
                proc_id = cur.lastrowid
            db.commit()
            flash("Procedimento salvo.", "ok")
            return redirect(url_for("procedimento", proc_id=proc_id))
    categorias = [r[0] for r in db.execute("SELECT DISTINCT categoria FROM procedimentos ORDER BY 1")]
    return render_template("procedimento_form.html", p=p, categorias=categorias)


@app.route("/procedimentos/<int:proc_id>/excluir", methods=["POST"])
@somente_rh
def procedimento_excluir(proc_id):
    db = get_db()
    db.execute("DELETE FROM procedimentos WHERE id = ?", (proc_id,))
    db.commit()
    flash("Procedimento excluído.", "ok")
    return redirect(url_for("procedimentos"))


# ---------------------------------------------------------------- processos de admissão e demissão

def listar_processos(abertos):
    filtro = "IS NULL" if abertos else "IS NOT NULL"
    return get_db().execute(
        "SELECT p.*, COUNT(i.id) AS total, COUNT(i.concluido_em) AS feitos, "
        "MIN(CASE WHEN i.concluido_em IS NULL THEN i.prazo END) AS proximo_prazo "
        f"FROM processos p LEFT JOIN processo_itens i ON i.processo_id = p.id WHERE p.encerrado_em {filtro} "
        "GROUP BY p.id ORDER BY p.data_referencia DESC").fetchall()


@app.route("/processos")
@login_obrigatorio
def processos():
    return render_template("processos.html", abertos=listar_processos(True), encerrados=listar_processos(False))


@app.route("/processos/novo", methods=["GET", "POST"])
@somente_rh
def processo_novo():
    if request.method == "POST":
        f = request.form
        tipo, colaborador, ref = f.get("tipo"), f.get("colaborador", "").strip(), parse_data(f.get("data_referencia"))
        if tipo not in TIPOS_PROCESSO or not colaborador or not ref:
            flash("Preencha tipo, colaborador e data.", "erro")
        else:
            db = get_db()
            cur = db.execute(
                "INSERT INTO processos (tipo, colaborador, cargo, gestor, data_referencia, observacoes, criado_em) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tipo, colaborador, f.get("cargo", "").strip(), f.get("gestor", "").strip(), ref.isoformat(),
                 f.get("observacoes", "").strip(), agora()))
            modelo = db.execute("SELECT * FROM modelo_itens WHERE tipo = ? ORDER BY ordem", (tipo,)).fetchall()
            for item in modelo:
                db.execute("INSERT INTO processo_itens (processo_id, ordem, descricao, prazo) VALUES (?, ?, ?, ?)",
                           (cur.lastrowid, item["ordem"], item["descricao"],
                            (ref + timedelta(days=item["dias_relativos"])).isoformat()))
            db.commit()
            flash("Processo criado com o checklist do modelo.", "ok")
            return redirect(url_for("processo", processo_id=cur.lastrowid))
    return render_template("processo_novo.html", tipo=request.args.get("tipo", "admissao"))


@app.route("/processos/<int:processo_id>")
@login_obrigatorio
def processo(processo_id):
    db = get_db()
    p = db.execute("SELECT * FROM processos WHERE id = ?", (processo_id,)).fetchone() or abort(404)
    itens = db.execute("SELECT * FROM processo_itens WHERE processo_id = ? ORDER BY prazo, ordem",
                       (processo_id,)).fetchall()
    return render_template("processo.html", p=p, itens=itens)


@app.route("/processos/<int:processo_id>/itens/<int:item_id>/alternar", methods=["POST"])
@login_obrigatorio
def processo_item_alternar(processo_id, item_id):
    db = get_db()
    item = db.execute("SELECT * FROM processo_itens WHERE id = ? AND processo_id = ?",
                      (item_id, processo_id)).fetchone() or abort(404)
    if item["concluido_em"]:
        db.execute("UPDATE processo_itens SET concluido_em = NULL, concluido_por = NULL WHERE id = ?", (item_id,))
    else:
        db.execute("UPDATE processo_itens SET concluido_em = ?, concluido_por = ? WHERE id = ?",
                   (agora(), g.usuario["nome"], item_id))
    db.commit()
    return redirect(request.form.get("voltar") or url_for("processo", processo_id=processo_id))


@app.route("/processos/<int:processo_id>/itens/novo", methods=["POST"])
@somente_rh
def processo_item_novo(processo_id):
    descricao, prazo = request.form.get("descricao", "").strip(), parse_data(request.form.get("prazo"))
    if descricao and prazo:
        db = get_db()
        db.execute("INSERT INTO processo_itens (processo_id, ordem, descricao, prazo) VALUES (?, 999, ?, ?)",
                   (processo_id, descricao, prazo.isoformat()))
        db.commit()
    else:
        flash("Informe a descrição e o prazo da tarefa.", "erro")
    return redirect(url_for("processo", processo_id=processo_id))


@app.route("/processos/<int:processo_id>/encerrar", methods=["POST"])
@somente_rh
def processo_encerrar(processo_id):
    db = get_db()
    p = db.execute("SELECT * FROM processos WHERE id = ?", (processo_id,)).fetchone() or abort(404)
    db.execute("UPDATE processos SET encerrado_em = ? WHERE id = ?",
               (None if p["encerrado_em"] else agora(), processo_id))
    db.commit()
    flash("Processo reaberto." if p["encerrado_em"] else "Processo encerrado.", "ok")
    return redirect(url_for("processo", processo_id=processo_id))


@app.route("/processos/<int:processo_id>/excluir", methods=["POST"])
@somente_rh
def processo_excluir(processo_id):
    db = get_db()
    db.execute("DELETE FROM processos WHERE id = ?", (processo_id,))
    db.commit()
    flash("Processo excluído.", "ok")
    return redirect(url_for("processos"))


@app.route("/modelos", methods=["GET", "POST"])
@somente_rh
def modelos():
    db = get_db()
    if request.method == "POST":
        tipo, descricao = request.form.get("tipo"), request.form.get("descricao", "").strip()
        try:
            dias = int(request.form.get("dias_relativos", "0"))
        except ValueError:
            dias = None
        if tipo in TIPOS_PROCESSO and descricao and dias is not None:
            ordem = db.execute("SELECT COALESCE(MAX(ordem), -1) + 1 FROM modelo_itens WHERE tipo = ?",
                               (tipo,)).fetchone()[0]
            db.execute("INSERT INTO modelo_itens (tipo, ordem, descricao, dias_relativos) VALUES (?, ?, ?, ?)",
                       (tipo, ordem, descricao, dias))
            db.commit()
            flash("Tarefa adicionada ao modelo.", "ok")
        else:
            flash("Informe a descrição e o número de dias.", "erro")
        return redirect(url_for("modelos"))
    itens = {t: db.execute("SELECT * FROM modelo_itens WHERE tipo = ? ORDER BY dias_relativos, ordem",
                           (t,)).fetchall() for t in TIPOS_PROCESSO}
    return render_template("modelos.html", itens=itens)


@app.route("/modelos/<int:item_id>/excluir", methods=["POST"])
@somente_rh
def modelo_excluir(item_id):
    db = get_db()
    db.execute("DELETE FROM modelo_itens WHERE id = ?", (item_id,))
    db.commit()
    return redirect(url_for("modelos"))


# ---------------------------------------------------------------- usuários

@app.route("/usuarios", methods=["GET", "POST"])
@somente_rh
def usuarios():
    db = get_db()
    if request.method == "POST":
        f = request.form
        nome, email, senha, papel = (f.get("nome", "").strip(), f.get("email", "").strip().lower(),
                                     f.get("senha", ""), f.get("papel"))
        if not nome or not email or len(senha) < 8 or papel not in PAPEIS:
            flash("Preencha todos os campos. A senha deve ter pelo menos 8 caracteres.", "erro")
        elif db.execute("SELECT 1 FROM usuarios WHERE email = ?", (email,)).fetchone():
            flash("Já existe um usuário com esse e-mail.", "erro")
        else:
            db.execute("INSERT INTO usuarios (nome, email, senha_hash, papel) VALUES (?, ?, ?, ?)",
                       (nome, email, generate_password_hash(senha), papel))
            db.commit()
            flash("Usuário criado. Informe a senha inicial à pessoa.", "ok")
        return redirect(url_for("usuarios"))
    lista = db.execute("SELECT * FROM usuarios ORDER BY ativo DESC, nome").fetchall()
    return render_template("usuarios.html", lista=lista)


@app.route("/usuarios/<int:usuario_id>/alternar", methods=["POST"])
@somente_rh
def usuario_alternar(usuario_id):
    if usuario_id == g.usuario["id"]:
        flash("Você não pode desativar o próprio usuário.", "erro")
    else:
        db = get_db()
        db.execute("UPDATE usuarios SET ativo = 1 - ativo WHERE id = ?", (usuario_id,))
        db.commit()
    return redirect(url_for("usuarios"))


@app.route("/minha-senha", methods=["GET", "POST"])
@login_obrigatorio
def minha_senha():
    if request.method == "POST":
        atual, nova = request.form.get("atual", ""), request.form.get("nova", "")
        if not check_password_hash(g.usuario["senha_hash"], atual):
            flash("Senha atual incorreta.", "erro")
        elif len(nova) < 8:
            flash("A nova senha deve ter pelo menos 8 caracteres.", "erro")
        else:
            db = get_db()
            db.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?",
                       (generate_password_hash(nova), g.usuario["id"]))
            db.commit()
            flash("Senha alterada.", "ok")
            return redirect(url_for("painel"))
    return render_template("minha_senha.html")


init_db()

if __name__ == "__main__":
    app.run(host=os.environ.get("RH_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("RH_DEBUG") == "1")
