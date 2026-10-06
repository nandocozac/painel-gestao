import os
import csv
import re
import time
import shutil
import uuid
import zipfile
from io import StringIO, BytesIO
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_file, session, g, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text, event
from sqlalchemy.engine import Engine
from database import db
from models import Empresa, Cliente, Veiculo, OrdemServico, Transacao, ItemOS, NotaFiscal, Produto

# Diretórios e caminhos absolutos para compatibilidade total local e em produção (Hostinger / LiteSpeed)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_DIR, exist_ok=True)
DB_PATH = os.path.join(INSTANCE_DIR, 'painel_gestao.db').replace('\\', '/')

app = Flask(__name__, instance_path=INSTANCE_DIR)
app.config['SECRET_KEY'] = 'chave-segura-painel-interno-multiempresa-2026'
# Conexão de Banco de Dados: Suporta PostgreSQL na nuvem (Render) ou SQLite local
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
    elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_NAME'] = 'painel_gestao_session'

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db.init_app(app)

# Otimização de concorrência e integridade SQLite para múltiplos workers / acessos simultâneos
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.close()


# --- MIGRAÇÃO AUTOMÁTICA MULTI-EMPRESA E ADMINISTRAÇÃO SAAS ---

def migrar_banco_multiempresa():
    with app.app_context():
        db.create_all()

        is_sqlite = 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']
        if is_sqlite:
            # 1. Garantir que as novas colunas existam na tabela 'empresas' (execução antes de qualquer query SQLAlchemy no SQLite)
            colunas_empresas = [row[1] for row in db.session.execute(text("PRAGMA table_info(empresas)")).fetchall()]
            novas_colunas_empresas = {
                'is_admin': 'BOOLEAN DEFAULT 0',
                'status_assinatura': 'VARCHAR(20) DEFAULT "PENDENTE"',
                'data_validade': 'DATE',
                'observacoes_admin': 'TEXT DEFAULT ""',
                'chave_pix': 'VARCHAR(100) DEFAULT ""',
                'titular_pix': 'VARCHAR(100) DEFAULT "Fernando Cozac"',
                'valor_mensalidade': 'FLOAT DEFAULT 29.90',
                'valor_anual': 'FLOAT DEFAULT 249.90',
                'valor_mensalidade_fiscal': 'FLOAT DEFAULT 79.90',
                'valor_anual_fiscal': 'FLOAT DEFAULT 699.90',
                'tipo_negocio': 'VARCHAR(30) DEFAULT "OFICINA"',
                'permite_emissao_fiscal': 'BOOLEAN DEFAULT 0',
                'fiscal_ativo': 'BOOLEAN DEFAULT 0',
                'fiscal_ambiente': 'VARCHAR(20) DEFAULT "HOMOLOGACAO"',
                'fiscal_cnpj': 'VARCHAR(20) DEFAULT ""',
                'fiscal_razao_social': 'VARCHAR(150) DEFAULT ""',
                'fiscal_nome_fantasia': 'VARCHAR(150) DEFAULT ""',
                'fiscal_inscricao_municipal': 'VARCHAR(30) DEFAULT ""',
                'fiscal_inscricao_estadual': 'VARCHAR(30) DEFAULT ""',
                'fiscal_codigo_municipio': 'VARCHAR(10) DEFAULT "5208707"',
                'fiscal_regime_tributario': 'INTEGER DEFAULT 1',
                'fiscal_certificado_filename': 'VARCHAR(255)',
                'fiscal_certificado_senha': 'VARCHAR(255) DEFAULT ""',
                'fiscal_token_focus': 'VARCHAR(100) DEFAULT ""'
            }
            for col, col_type in novas_colunas_empresas.items():
                if col not in colunas_empresas:
                    db.session.execute(text(f"ALTER TABLE empresas ADD COLUMN {col} {col_type}"))
            db.session.commit()

            # 2. Garantir que as colunas 'empresa_id' existam nas tabelas de dados
            tabelas = ['clientes', 'ordens_servico', 'transacoes']
            for tab in tabelas:
                colunas = [row[1] for row in db.session.execute(text(f"PRAGMA table_info({tab})")).fetchall()]
                if 'empresa_id' not in colunas:
                    db.session.execute(text(f"ALTER TABLE {tab} ADD COLUMN empresa_id INTEGER REFERENCES empresas(id)"))
            db.session.commit()

            # 3. Garantir colunas fiscais e de produtos na tabela itens_os
            colunas_itens = [row[1] for row in db.session.execute(text("PRAGMA table_info(itens_os)")).fetchall()]
            novas_colunas_itens = {
                'produto_id': 'INTEGER REFERENCES produtos(id)',
                'ncm': 'VARCHAR(10) DEFAULT ""',
                'cfop': 'VARCHAR(10) DEFAULT ""',
                'codigo_servico_municipal': 'VARCHAR(20) DEFAULT ""',
                'aliquota_iss': 'FLOAT DEFAULT 0.0'
            }
            for col, col_type in novas_colunas_itens.items():
                if col not in colunas_itens:
                    db.session.execute(text(f"ALTER TABLE itens_os ADD COLUMN {col} {col_type}"))
            db.session.commit()

            # 4. Garantir colunas de rastreio e assinatura na tabela ordens_servico (SQLite)
            colunas_os = [row[1] for row in db.session.execute(text("PRAGMA table_info(ordens_servico)")).fetchall()]
            novas_colunas_os = {
                'etapa_andamento': 'VARCHAR(30) DEFAULT "RECEBIDO"',
                'codigo_rastreio': 'VARCHAR(32)',
                'assinatura_cliente_data': 'TEXT',
                'assinatura_data_hora': 'TIMESTAMP'
            }
            for col, col_type in novas_colunas_os.items():
                if col not in colunas_os:
                    db.session.execute(text(f"ALTER TABLE ordens_servico ADD COLUMN {col} {col_type}"))
            db.session.commit()

            # 5. Garantir colunas de CPF/CNPJ e endereço na tabela clientes (SQLite)
            colunas_clientes = [row[1] for row in db.session.execute(text("PRAGMA table_info(clientes)")).fetchall()]
            novas_colunas_clientes = {
                'cpf_cnpj': 'VARCHAR(20) DEFAULT ""',
                'email': 'VARCHAR(120) DEFAULT ""',
                'endereco': 'VARCHAR(200) DEFAULT ""',
                'numero': 'VARCHAR(20) DEFAULT ""',
                'bairro': 'VARCHAR(100) DEFAULT ""',
                'cidade': 'VARCHAR(100) DEFAULT ""',
                'uf': 'VARCHAR(2) DEFAULT ""',
                'cep': 'VARCHAR(10) DEFAULT ""'
            }
            for col, col_type in novas_colunas_clientes.items():
                if col not in colunas_clientes:
                    db.session.execute(text(f"ALTER TABLE clientes ADD COLUMN {col} {col_type}"))
            db.session.commit()
        else:
            # Migração para PostgreSQL (Render Cloud)
            pg_colunas_empresas = {
                'is_admin': 'BOOLEAN DEFAULT FALSE',
                'status_assinatura': "VARCHAR(20) DEFAULT 'PENDENTE'",
                'data_validade': 'DATE',
                'observacoes_admin': "TEXT DEFAULT ''",
                'chave_pix': "VARCHAR(100) DEFAULT ''",
                'titular_pix': "VARCHAR(100) DEFAULT 'Fernando Cozac'",
                'valor_mensalidade': 'FLOAT DEFAULT 29.90',
                'valor_anual': 'FLOAT DEFAULT 249.90',
                'valor_mensalidade_fiscal': 'FLOAT DEFAULT 79.90',
                'valor_anual_fiscal': 'FLOAT DEFAULT 699.90',
                'tipo_negocio': "VARCHAR(30) DEFAULT 'OFICINA'",
                'permite_emissao_fiscal': 'BOOLEAN DEFAULT FALSE',
                'fiscal_ativo': 'BOOLEAN DEFAULT FALSE',
                'fiscal_ambiente': "VARCHAR(20) DEFAULT 'HOMOLOGACAO'",
                'fiscal_cnpj': "VARCHAR(20) DEFAULT ''",
                'fiscal_razao_social': "VARCHAR(150) DEFAULT ''",
                'fiscal_nome_fantasia': "VARCHAR(150) DEFAULT ''",
                'fiscal_inscricao_municipal': "VARCHAR(30) DEFAULT ''",
                'fiscal_inscricao_estadual': "VARCHAR(30) DEFAULT ''",
                'fiscal_codigo_municipio': "VARCHAR(10) DEFAULT '5208707'",
                'fiscal_regime_tributario': 'INTEGER DEFAULT 1',
                'fiscal_certificado_filename': 'VARCHAR(255)',
                'fiscal_certificado_senha': "VARCHAR(255) DEFAULT ''",
                'fiscal_token_focus': "VARCHAR(100) DEFAULT ''"
            }
            for col, col_type in pg_colunas_empresas.items():
                try:
                    db.session.execute(text(f"ALTER TABLE empresas ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            tabelas = ['clientes', 'ordens_servico', 'transacoes']
            for tab in tabelas:
                try:
                    db.session.execute(text(f"ALTER TABLE {tab} ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES empresas(id)"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            pg_colunas_itens = {
                'produto_id': "INTEGER REFERENCES produtos(id)",
                'ncm': "VARCHAR(10) DEFAULT ''",
                'cfop': "VARCHAR(10) DEFAULT ''",
                'codigo_servico_municipal': "VARCHAR(20) DEFAULT ''",
                'aliquota_iss': 'FLOAT DEFAULT 0.0'
            }
            for col, col_type in pg_colunas_itens.items():
                try:
                    db.session.execute(text(f"ALTER TABLE itens_os ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            pg_colunas_os = {
                'etapa_andamento': "VARCHAR(30) DEFAULT 'RECEBIDO'",
                'codigo_rastreio': "VARCHAR(32)",
                'assinatura_cliente_data': "TEXT",
                'assinatura_data_hora': "TIMESTAMP"
            }
            for col, col_type in pg_colunas_os.items():
                try:
                    db.session.execute(text(f"ALTER TABLE ordens_servico ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            pg_colunas_clientes = {
                'cpf_cnpj': "VARCHAR(20) DEFAULT ''",
                'email': "VARCHAR(120) DEFAULT ''",
                'endereco': "VARCHAR(200) DEFAULT ''",
                'numero': "VARCHAR(20) DEFAULT ''",
                'bairro': "VARCHAR(100) DEFAULT ''",
                'cidade': "VARCHAR(100) DEFAULT ''",
                'uf': "VARCHAR(2) DEFAULT ''",
                'cep': "VARCHAR(10) DEFAULT ''"
            }
            for col, col_type in pg_colunas_clientes.items():
                try:
                    db.session.execute(text(f"ALTER TABLE clientes ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        # 3. Garantir que a empresa Master exista de forma segura
        empresa_padrao = Empresa.query.filter_by(is_admin=True).first()
        if not empresa_padrao:
            empresa_padrao = Empresa.query.filter_by(email="nandocozac@gmail.com").first()
        if not empresa_padrao:
            empresa_padrao = Empresa.query.order_by(Empresa.id.asc()).first()

        if not empresa_padrao:
            empresa_padrao = Empresa(
                nome_empresa="SPOT MARKETING",
                subtitulo="Serviços Especializados e Atendimento Profissional",
                telefone="(62) 99494-1212",
                whatsapp="62994941212",
                endereco="",
                cidade_uf="",
                logo_filename=None,
                mensagem_rodape="Agradecemos a preferência! Volte sempre.",
                email="nandocozac@gmail.com",
                senha_hash=generate_password_hash("Matheus10#"),
                is_admin=True,
                status_assinatura="ATIVO",
                chave_pix="nandocozac@gmail.com",
                titular_pix="Fernando Cozac",
                valor_mensalidade=29.90,
                valor_anual=249.90,
                data_validade=date(2099, 12, 31)
            )
            db.session.add(empresa_padrao)
            db.session.commit()
        else:
            # Garantir que a empresa master continue sempre Master Admin e Ativa
            mudou = False
            if not empresa_padrao.is_admin:
                empresa_padrao.is_admin = True
                mudou = True
            if empresa_padrao.status_assinatura != 'ATIVO':
                empresa_padrao.status_assinatura = 'ATIVO'
                mudou = True
            if not empresa_padrao.data_validade or empresa_padrao.data_validade < date.today():
                empresa_padrao.data_validade = date(2099, 12, 31)
                mudou = True
            if mudou:
                db.session.commit()

        # 4. Vincular dados anteriores existentes à empresa padrão
        try:
            db.session.execute(text(f"UPDATE clientes SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
            db.session.execute(text(f"UPDATE ordens_servico SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
            db.session.execute(text(f"UPDATE transacoes SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
            # 5. Garantir que nenhuma empresa tenha 'logo.png' como padrão (sem imagem prévia até o cliente fazer upload)
            db.session.execute(text("UPDATE empresas SET logo_filename = NULL WHERE logo_filename = 'logo.png'"))
            db.session.commit()

            # 6. Gerar código de rastreio para ordens de serviço existentes que ainda não tenham
            os_sem_rastreio = OrdemServico.query.filter((OrdemServico.codigo_rastreio == None) | (OrdemServico.codigo_rastreio == '')).all()
            for os_item in os_sem_rastreio:
                os_item.codigo_rastreio = uuid.uuid4().hex[:12]
                if not os_item.etapa_andamento:
                    os_item.etapa_andamento = 'ENTREGUE' if os_item.status == 'CONCLUIDA' else 'RECEBIDO'
            if os_sem_rastreio:
                db.session.commit()
        except Exception:
            db.session.rollback()

migrar_banco_multiempresa()

def recuperar_banco_raiz_se_existir():
    """Garante que se existir um painel_gestao.db antigo na raiz do Hostinger, os dados sejam migrados para instance/"""
    if 'sqlite' not in app.config['SQLALCHEMY_DATABASE_URI']:
        return
    root_db = os.path.join(BASE_DIR, 'painel_gestao.db')
    if os.path.exists(root_db) and os.path.abspath(root_db) != os.path.abspath(DB_PATH):
        try:
            import sqlite3
            con_root = sqlite3.connect(root_db)
            cur_root = con_root.cursor()
            
            tabelas = [r[0] for r in cur_root.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if 'empresas' in tabelas:
                empresas_root = cur_root.execute("SELECT email, nome_empresa, senha_hash, telefone, whatsapp, status_assinatura, data_validade, is_admin FROM empresas").fetchall()
                with app.app_context():
                    for emp_data in empresas_root:
                        email, nome, senha_hash, tel, wa, status, validade, is_adm = emp_data
                        if not is_adm and email:
                            ja_existe = Empresa.query.filter_by(email=email).first()
                            if not ja_existe:
                                val_date = None
                                if validade:
                                    try:
                                        val_date = date.fromisoformat(str(validade)[:10])
                                    except Exception:
                                        val_date = date.today() + timedelta(days=30)
                                nova = Empresa(
                                    nome_empresa=nome or "Empresa Recuperada",
                                    email=email,
                                    senha_hash=senha_hash,
                                    telefone=tel or "",
                                    whatsapp=wa or "",
                                    status_assinatura=status or "ATIVO",
                                    data_validade=val_date,
                                    is_admin=False
                                )
                                db.session.add(nova)
                    db.session.commit()
            con_root.close()
        except Exception:
            pass

recuperar_banco_raiz_se_existir()


# --- CONTROLE DE SESSÃO E IDENTIFICAÇÃO DO USUÁRIO ---

@app.before_request
def carregar_empresa_logada():
    empresa_id = session.get('empresa_id')
    if empresa_id:
        g.empresa = db.session.get(Empresa, empresa_id)
        if not g.empresa:
            session.pop('empresa_id', None)
            g.empresa = None
    else:
        g.empresa = None


@app.after_request
def add_no_cache_headers(response):
    """
    Garante que LiteSpeed, proxies intermediários e navegadores NUNCA façam cache de páginas dinâmicas ou cookies.
    Isso é vital para evitar vazamento ou compartilhamento de telas/sessões entre diferentes computadores na hospedagem.
    """
    if not request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0, private'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['X-LiteSpeed-Cache-Control'] = 'no-cache'
    return response


@app.context_processor
def inject_empresa():
    return dict(empresa=getattr(g, 'empresa', None))


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not getattr(g, 'empresa', None):
            flash('Por favor, faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login', next=request.path))

        # Verificação de Bloqueio por Pagamento / Assinatura (exceto para o Dono Master)
        if not g.empresa.is_admin:
            hoje = date.today()
            esta_bloqueada = (g.empresa.status_assinatura != 'ATIVO') or (g.empresa.data_validade and g.empresa.data_validade < hoje)
            if esta_bloqueada:
                return redirect(url_for('assinatura_bloqueada'))

        return view_func(*args, **kwargs)
    return wrapped_view


def admin_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not getattr(g, 'empresa', None) or not g.empresa.is_admin:
            flash('Acesso restrito ao Administrador Master.', 'error')
            return redirect(url_for('dashboard'))
        return view_func(*args, **kwargs)
    return wrapped_view


# --- FILTROS DE FORMATAÇÃO (JINJA2) ---

@app.template_filter('moeda')
def formato_moeda(valor):
    try:
        val = float(valor or 0.0)
        return f"{val:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return "0,00"

@app.template_filter('data_br')
def formato_data(data_obj):
    if not data_obj:
        return "-"
    return data_obj.strftime('%d/%m/%Y')

def limpar_telefone_wa(telefone):
    if not telefone:
        return ""
    nums = re.sub(r'\D', '', str(telefone))
    if len(nums) in (10, 11):
        nums = '55' + nums
    return nums

@app.template_filter('fone_whatsapp')
def fone_whatsapp_filter(telefone):
    return limpar_telefone_wa(telefone)

@app.template_filter('msg_whatsapp_os')
def msg_whatsapp_os_filter(os_obj):
    cliente_nome = os_obj.cliente.nome.strip() if os_obj.cliente else "Cliente"
    item_info = f"{os_obj.veiculo.modelo} ({os_obj.veiculo.placa})" if os_obj.veiculo else "item / serviço"
    empresa = os_obj.empresa or getattr(g, 'empresa', None)
    nome_empresa = empresa.nome_empresa if empresa else "Empresa"
    
    linhas = [
        f"📋 *{nome_empresa.upper()}*",
        f"Olá, *{cliente_nome}*! 👋\n",
        f"Sua Ordem de Serviço *#{os_obj.id:04d}* foi finalizada e seu atendimento referente a *{item_info}* está concluído! ✅\n",
        "📋 *RESUMO DO ATENDIMENTO:*"
    ]
    
    pecas = [i for i in os_obj.itens if i.tipo == 'PECA']
    if pecas:
        linhas.append("\n📦 *Produtos / Peças / Materiais:*")
        for p in pecas:
            qtd_str = f"{p.quantidade:g}"
            preco_unit = formato_moeda(p.valor_unitario)
            sub_total = formato_moeda(p.subtotal)
            linhas.append(f"• {p.descricao} ({qtd_str}x R$ {preco_unit}) = R$ {sub_total}")
        linhas.append(f"  *Subtotal Materiais:* R$ {formato_moeda(os_obj.valor_pecas)}")
        
    servicos = [i for i in os_obj.itens if i.tipo == 'SERVICO']
    if servicos:
        linhas.append("\n🛠️ *Mão de Obra / Serviços Executados:*")
        for s in servicos:
            sub_total = formato_moeda(s.subtotal)
            linhas.append(f"• {s.descricao} = R$ {sub_total}")
        linhas.append(f"  *Subtotal Serviços:* R$ {formato_moeda(os_obj.valor_mao_obra)}")
        
    if os_obj.servico_executado:
        linhas.append(f"\n📝 *Observações / Laudo Técnico:*\n{os_obj.servico_executado}")
        
    linhas.append("\n" + ("─" * 22))
    linhas.append(f"💰 *VALOR TOTAL: R$ {formato_moeda(os_obj.valor_total)}*")
    if os_obj.forma_pagamento:
        linhas.append(f"💳 *Forma de Pagamento:* {os_obj.forma_pagamento}")
    if os_obj.retorno_previsto:
        linhas.append(f"📅 *Próxima Revisão Preventiva Sugerida:* {os_obj.retorno_previsto.strftime('%d/%m/%Y')}")
    linhas.append("🛡️ *Garantia:* 90 dias sobre serviços e materiais aplicados.")
    if os_obj.codigo_rastreio:
        linhas.append(f"📲 *Acompanhe seu atendimento online:* https://nandocozac.shop/status/{os_obj.codigo_rastreio}")
    if empresa and empresa.endereco:
        loc = empresa.endereco
        if empresa.cidade_uf:
            loc += f" - {empresa.cidade_uf}"
        linhas.append(f"\n📍 *Endereço:* {loc}")
    if empresa and (empresa.telefone or empresa.whatsapp):
        linhas.append(f"📞 *Contato:* {empresa.whatsapp or empresa.telefone}")
    linhas.append("Aguardamos você para a retirada / entrega. Qualquer dúvida estamos à disposição! 🤝")
    
    return "\n".join(linhas)


# --- AUTENTICAÇÃO (LOGIN, CADASTRO, LOGOUT) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if getattr(g, 'empresa', None):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '').strip()

        empresa = Empresa.query.filter_by(email=email).first()
        if empresa and check_password_hash(empresa.senha_hash, senha):
            session['empresa_id'] = empresa.id
            
            # Se for empresa comum e estiver pendente ou bloqueada, informa com clareza
            if not empresa.is_admin:
                hoje = date.today()
                esta_bloqueada = (empresa.status_assinatura != 'ATIVO') or (empresa.data_validade and empresa.data_validade < hoje)
                if esta_bloqueada:
                    if empresa.status_assinatura == 'PENDENTE':
                        flash(f"Conta encontrada! Seu cadastro está concluído e aguarda confirmação de pagamento para liberar o acesso.", "info")
                    else:
                        flash("Sua assinatura está expirada ou bloqueada. Efetue o pagamento para renovar o acesso.", "warning")
                    return redirect(url_for('assinatura_bloqueada'))

            flash(f"Bem-vindo(a), {empresa.nome_empresa}!", "success")
            next_url = request.args.get('next')
            return redirect(next_url or url_for('dashboard'))
        else:
            flash("E-mail ou senha incorretos. Tente novamente.", "error")

    return render_template('login.html')


@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if getattr(g, 'empresa', None):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        nome_empresa = request.form.get('nome_empresa', '').strip()
        email = request.form.get('email', '').strip().lower()
        telefone = request.form.get('telefone', '').strip()
        senha = request.form.get('senha', '').strip()
        confirmar_senha = request.form.get('confirmar_senha', '').strip()

        if not nome_empresa or not email or not senha:
            flash("Preencha todos os campos obrigatórios.", "error")
            return render_template('cadastro.html')

        if senha != confirmar_senha:
            flash("As senhas não coincidem.", "error")
            return render_template('cadastro.html')

        if len(senha) < 6:
            flash("A senha deve conter no mínimo 6 caracteres.", "error")
            return render_template('cadastro.html')

        if Empresa.query.filter_by(email=email).first():
            flash("Este e-mail já está cadastrado. Faça login ou use outro e-mail.", "error")
            return render_template('cadastro.html')

        tipo_negocio = request.form.get('tipo_negocio', 'OFICINA').strip().upper()
        if tipo_negocio not in ['OFICINA', 'LOJA']:
            tipo_negocio = 'OFICINA'

        # Nova empresa cadastrada fica PENDENTE aguardando aprovação/pagamento do Pix
        nova_empresa = Empresa(
            nome_empresa=nome_empresa,
            email=email,
            telefone=telefone,
            whatsapp=telefone,
            senha_hash=generate_password_hash(senha),
            is_admin=False,
            tipo_negocio=tipo_negocio,
            status_assinatura="PENDENTE",
            logo_filename=None,
            mensagem_rodape="Agradecemos a preferência! Volte sempre."
        )
        db.session.add(nova_empresa)
        db.session.commit()

        session['empresa_id'] = nova_empresa.id
        flash(f"Conta da empresa '{nova_empresa.nome_empresa}' criada com sucesso! Siga as instruções abaixo para ativar seu acesso.", "info")
        return redirect(url_for('assinatura_bloqueada'))

    return render_template('cadastro.html')


@app.route('/logout')
def logout():
    session.pop('empresa_id', None)
    flash("Sua sessão foi encerrada com segurança.", "info")
    return redirect(url_for('login'))


# --- TELA DE BLOQUEIO POR FALTA DE PAGAMENTO ---

@app.route('/assinatura/bloqueada')
def assinatura_bloqueada():
    if not getattr(g, 'empresa', None):
        return redirect(url_for('login'))

    # Se for o Master Admin, nunca é bloqueado
    if g.empresa.is_admin:
        return redirect(url_for('dashboard'))

    hoje = date.today()
    esta_bloqueada = (g.empresa.status_assinatura != 'ATIVO') or (g.empresa.data_validade and g.empresa.data_validade < hoje)
    if not esta_bloqueada:
        return redirect(url_for('dashboard'))

    master_empresa = Empresa.query.filter_by(is_admin=True).first()
    return render_template('bloqueado.html', empresa=g.empresa, master_empresa=master_empresa)


# --- PAINEL DO ADMINISTRADOR MASTER (DONO DA PLATAFORMA) ---

@app.route('/admin/empresas')
@login_required
@admin_required
def admin_empresas():
    empresas = Empresa.query.order_by(Empresa.id.desc()).all()
    db_exists = os.path.exists(DB_PATH)
    db_size_kb = round(os.path.getsize(DB_PATH) / 1024, 1) if db_exists else 0
    db_mtime = time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(os.path.getmtime(DB_PATH))) if db_exists else 'N/A'
    total_clientes = Cliente.query.count()
    total_os = OrdemServico.query.count()
    return render_template(
        'admin_empresas.html',
        empresas=empresas,
        hoje=date.today(),
        db_path=DB_PATH,
        db_size_kb=db_size_kb,
        db_mtime=db_mtime,
        total_clientes=total_clientes,
        total_os=total_os
    )


@app.route('/admin/empresa/nova', methods=['POST'])
@login_required
@admin_required
def admin_nova_empresa():
    nome = request.form.get('nome_empresa', '').strip()
    email = request.form.get('email', '').strip().lower()
    telefone = request.form.get('telefone', '').strip()
    senha = request.form.get('senha', '').strip()
    status = request.form.get('status', 'ATIVO').strip()
    dias_validade = int(request.form.get('dias_validade', 30))

    if not nome or not email or not senha:
        flash("Nome, e-mail e senha são obrigatórios.", "error")
        return redirect(url_for('admin_empresas'))

    if Empresa.query.filter_by(email=email).first():
        flash("Este e-mail já está cadastrado em outra empresa.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    tipo_negocio = request.form.get('tipo_negocio', 'OFICINA').strip().upper()
    if tipo_negocio not in ['OFICINA', 'LOJA']:
        tipo_negocio = 'OFICINA'

    nova_emp = Empresa(
        nome_empresa=nome,
        email=email,
        telefone=telefone,
        whatsapp=telefone,
        senha_hash=generate_password_hash(senha),
        is_admin=False,
        tipo_negocio=tipo_negocio,
        status_assinatura=status,
        data_validade=hoje + timedelta(days=dias_validade) if status == 'ATIVO' else None,
        logo_filename=None,
        mensagem_rodape="Agradecemos a preferência! Volte sempre."
    )
    db.session.add(nova_emp)
    db.session.commit()

    flash(f"Empresa '{nome}' cadastrada com sucesso com status '{status}'!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/cobranca', methods=['POST'])
@login_required
@admin_required
def admin_salvar_cobranca():
    g.empresa.chave_pix = request.form.get('chave_pix', '').strip()
    g.empresa.titular_pix = request.form.get('titular_pix', '').strip()
    try:
        val_m = float(request.form.get('valor_mensalidade', '29.90').replace(',', '.'))
        g.empresa.valor_mensalidade = round(val_m, 2)
    except (ValueError, TypeError):
        pass

    try:
        val_a = float(request.form.get('valor_anual', '249.90').replace(',', '.'))
        g.empresa.valor_anual = round(val_a, 2)
    except (ValueError, TypeError):
        pass

    try:
        val_mf = float(request.form.get('valor_mensalidade_fiscal', '79.90').replace(',', '.'))
        g.empresa.valor_mensalidade_fiscal = round(val_mf, 2)
    except (ValueError, TypeError):
        pass

    try:
        val_af = float(request.form.get('valor_anual_fiscal', '699.90').replace(',', '.'))
        g.empresa.valor_anual_fiscal = round(val_af, 2)
    except (ValueError, TypeError):
        pass

    db.session.commit()
    flash("Dados de cobrança e Pix atualizados com sucesso!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/alternar-fiscal', methods=['POST'])
@login_required
@admin_required
def admin_alternar_fiscal(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    emp.permite_emissao_fiscal = not emp.permite_emissao_fiscal
    db.session.commit()

    if emp.permite_emissao_fiscal:
        flash(f"Módulo Fiscal LIBERADO com sucesso para a empresa '{emp.nome_empresa}'!", "success")
    else:
        flash(f"Módulo Fiscal BLOQUEADO para a empresa '{emp.nome_empresa}'.", "info")

    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/renovar', methods=['POST'])
@login_required
@admin_required
def admin_renovar_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    base_data = emp.data_validade if (emp.data_validade and emp.data_validade >= hoje) else hoje
    emp.data_validade = base_data + timedelta(days=30)
    emp.status_assinatura = 'ATIVO'
    db.session.commit()

    flash(f"Acesso da empresa '{emp.nome_empresa}' estendido por +30 dias (Válido até {emp.data_validade.strftime('%d/%m/%Y')})!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/renovar-anual', methods=['POST'])
@login_required
@admin_required
def admin_renovar_empresa_anual(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    base_data = emp.data_validade if (emp.data_validade and emp.data_validade >= hoje) else hoje
    emp.data_validade = base_data + timedelta(days=365)
    emp.status_assinatura = 'ATIVO'
    db.session.commit()

    flash(f"Acesso ANUAL da empresa '{emp.nome_empresa}' estendido por +1 ano (Válido até {emp.data_validade.strftime('%d/%m/%Y')})!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/ativar', methods=['POST'])
@login_required
@admin_required
def admin_ativar_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    emp.status_assinatura = 'ATIVO'
    if not emp.data_validade or emp.data_validade < hoje:
        emp.data_validade = hoje + timedelta(days=30)

    db.session.commit()
    flash(f"Acesso da empresa '{emp.nome_empresa}' liberado com sucesso!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/bloquear', methods=['POST'])
@login_required
@admin_required
def admin_bloquear_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    emp.status_assinatura = 'BLOQUEADO'
    db.session.commit()
    flash(f"Acesso da empresa '{emp.nome_empresa}' foi bloqueado.", "warning")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/excluir', methods=['POST'])
@login_required
@admin_required
def admin_excluir_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    if emp.is_admin or emp.id == g.empresa.id:
        flash("Não é permitido excluir a conta Master Admin principal!", "error")
        return redirect(url_for('admin_empresas'))

    nome = emp.nome_empresa

    # 1. Excluir transações financeiras da empresa
    Transacao.query.filter_by(empresa_id=emp.id).delete()

    # 2. Excluir ordens de serviço (e seus itens de OS associados)
    ordens = OrdemServico.query.filter_by(empresa_id=emp.id).all()
    for os_item in ordens:
        ItemOS.query.filter_by(os_id=os_item.id).delete()
        db.session.delete(os_item)

    # 3. Excluir veículos e clientes da empresa
    clientes = Cliente.query.filter_by(empresa_id=emp.id).all()
    for c in clientes:
        Veiculo.query.filter_by(cliente_id=c.id).delete()
        db.session.delete(c)

    # 4. Excluir a própria empresa
    db.session.delete(emp)
    db.session.commit()

    flash(f"A empresa '{nome}' e todos os seus registros foram excluídos com sucesso.", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/banco/backup')
@login_required
@admin_required
def admin_backup_banco():
    if not os.path.exists(DB_PATH):
        flash("Arquivo de banco de dados não encontrado.", "error")
        return redirect(url_for('admin_empresas'))

    nome_arquivo = f"backup_painel_gestao_{date.today().strftime('%Y%m%d')}_{int(time.time())}.db"
    return send_file(DB_PATH, as_attachment=True, download_name=nome_arquivo)


@app.route('/admin/banco/restaurar', methods=['POST'])
@login_required
@admin_required
def admin_restaurar_banco():
    arquivo = request.files.get('arquivo_banco')
    if not arquivo or not arquivo.filename:
        flash("Nenhum arquivo de banco de dados foi selecionado.", "error")
        return redirect(url_for('admin_empresas'))

    if not arquivo.filename.lower().endswith(('.db', '.sqlite', '.sqlite3')):
        flash("Formato de arquivo inválido. Por favor, envie um arquivo com extensão .db ou .sqlite.", "error")
        return redirect(url_for('admin_empresas'))

    conteudo = arquivo.read()
    # Verifica assinatura do SQLite
    if not conteudo.startswith(b'SQLite format 3\x00'):
        flash("O arquivo enviado não é um banco de dados SQLite válido!", "error")
        return redirect(url_for('admin_empresas'))

    try:
        # 1. Faz backup automático do banco atual antes de sobrescrever
        if os.path.exists(DB_PATH):
            backup_emergencia = f"{DB_PATH}.seguranca_{int(time.time())}"
            shutil.copy2(DB_PATH, backup_emergencia)

        # 2. Fecha conexões ativas do SQLAlchemy
        db.session.remove()
        db.engine.dispose()

        # 3. Grava o novo banco
        with open(DB_PATH, 'wb') as f:
            f.write(conteudo)

        # 4. Roda as migrações automáticas para garantir todas as tabelas e colunas
        migrar_banco_multiempresa()

        flash("Banco de dados restaurado com sucesso! Os novos dados já estão em vigor.", "success")
    except Exception as e:
        flash(f"Erro ao restaurar banco de dados: {str(e)}", "error")

    return redirect(url_for('admin_empresas'))


# --- DASHBOARD ISOLADO POR EMPRESA ---

@app.route('/')
@login_required
def dashboard():
    hoje = date.today()
    empresa_id = g.empresa.id

    transacoes_hoje = Transacao.query.filter_by(empresa_id=empresa_id, data_movimento=hoje).all()
    receitas_hoje = sum(t.valor for t in transacoes_hoje if t.tipo == 'RECEITA')
    despesas_hoje = sum(t.valor for t in transacoes_hoje if t.tipo == 'DESPESA')
    saldo_hoje = receitas_hoje - despesas_hoje

    primeiro_dia_mes = hoje.replace(day=1)
    transacoes_mes = Transacao.query.filter(
        Transacao.empresa_id == empresa_id,
        Transacao.data_movimento >= primeiro_dia_mes
    ).all()
    receitas_mes = sum(t.valor for t in transacoes_mes if t.tipo == 'RECEITA')
    despesas_mes = sum(t.valor for t in transacoes_mes if t.tipo == 'DESPESA')
    faturamento_mes = receitas_mes - despesas_mes

    os_abertas = OrdemServico.query.filter_by(empresa_id=empresa_id, status='ABERTA').order_by(OrdemServico.id.desc()).all()

    return render_template(
        'dashboard.html',
        receitas_hoje=receitas_hoje,
        despesas_hoje=despesas_hoje,
        saldo_hoje=saldo_hoje,
        receitas_mes=receitas_mes,
        despesas_mes=despesas_mes,
        faturamento_mes=faturamento_mes,
        os_abertas=os_abertas
    )


# --- LANÇAMENTO RÁPIDO DE DESPESA ---

@app.route('/financeiro/despesa', methods=['POST'])
@login_required
def nova_despesa():
    descricao = request.form.get('descricao', '').strip()
    forma_pagamento = request.form.get('forma_pagamento', 'Dinheiro').strip()
    raw_valor = request.form.get('valor', '0').replace('.', '').replace(',', '.').strip()

    try:
        valor = float(raw_valor)
    except (ValueError, TypeError):
        valor = 0.0

    if descricao and valor > 0:
        nova = Transacao(
            empresa_id=g.empresa.id,
            tipo='DESPESA',
            descricao=descricao,
            valor=round(valor, 2),
            forma_pagamento=forma_pagamento,
            data_movimento=date.today()
        )
        db.session.add(nova)
        db.session.commit()

    return redirect(url_for('dashboard'))


# --- ORDENS DE SERVIÇO ---

@app.route('/os')
@login_required
def lista_os():
    status = request.args.get('status', '').strip()
    busca = request.args.get('busca', '').strip()

    query = OrdemServico.query.filter_by(empresa_id=g.empresa.id)

    if status:
        query = query.filter_by(status=status)

    if busca:
        query = query.join(Cliente).outerjoin(Veiculo).filter(
            (Cliente.nome.ilike(f'%{busca}%')) |
            (Cliente.telefone.ilike(f'%{busca}%')) |
            (Veiculo.placa.ilike(f'%{busca}%')) |
            (Veiculo.modelo.ilike(f'%{busca}%'))
        )

    todas_os = query.order_by(OrdemServico.id.desc()).all()
    return render_template('os_lista.html', todas_os=todas_os, status_atual=status, busca=busca)


@app.route('/os/nova', methods=['GET', 'POST'])
@login_required
def nova_os():
    if request.method == 'POST':
        nome = request.form.get('cliente_nome', '').strip()
        telefone = request.form.get('cliente_telefone', '').strip()
        placa = request.form.get('veiculo_placa', '').strip().upper()
        modelo = request.form.get('veiculo_modelo', '').strip()
        problema = request.form.get('problema', '').strip()

        cpf_cnpj = request.form.get('cliente_cpf_cnpj', '').strip()
        email = request.form.get('cliente_email', '').strip().lower()

        # Busca ou cadastra cliente restrito a esta empresa
        cliente = Cliente.query.filter_by(empresa_id=g.empresa.id, telefone=telefone).first()
        if not cliente:
            cliente = Cliente(empresa_id=g.empresa.id, nome=nome, telefone=telefone, cpf_cnpj=cpf_cnpj, email=email)
            db.session.add(cliente)
            db.session.flush()
        else:
            if cpf_cnpj and not cliente.cpf_cnpj:
                cliente.cpf_cnpj = cpf_cnpj
            if email and not cliente.email:
                cliente.email = email

        veiculo = None
        if placa:
            veiculo = Veiculo.query.join(Cliente).filter(
                Cliente.empresa_id == g.empresa.id,
                Veiculo.placa == placa
            ).first()
            if not veiculo:
                veiculo = Veiculo(cliente_id=cliente.id, placa=placa, modelo=modelo)
                db.session.add(veiculo)
                db.session.flush()

        os_nova = OrdemServico(
            empresa_id=g.empresa.id,
            cliente_id=cliente.id,
            veiculo_id=veiculo.id if veiculo else None,
            descricao_problema=problema,
            status='ABERTA',
            etapa_andamento='RECEBIDO',
            codigo_rastreio=uuid.uuid4().hex[:12],
            data_abertura=date.today()
        )
        db.session.add(os_nova)
        db.session.commit()

        return redirect(url_for('ver_os', os_id=os_nova.id))

    return render_template('os_form.html')


@app.route('/os/<int:os_id>')
@login_required
def ver_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    return render_template('os_print.html', os=os)


@app.route('/os/<int:os_id>/pdf')
def ver_os_pdf(os_id):
    # O PDF pode ser acessado pelo cliente via WhatsApp ou impresso pela empresa
    os = OrdemServico.query.get_or_404(os_id)
    empresa = os.empresa
    return render_template('os_cliente_pdf.html', os=os, empresa=empresa)


@app.route('/os/<int:os_id>/concluir', methods=['POST'])
@login_required
def concluir_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    if os.status == 'CONCLUIDA':
        return redirect(url_for('ver_os', os_id=os.id))

    def converter_valor(v):
        try:
            return max(0.0, float(str(v or '0').replace('.', '').replace(',', '.').strip()))
        except (ValueError, TypeError):
            return 0.0

    try:
        # 1. Processar Peças / Materiais
        peca_nomes = request.form.getlist('peca_nome[]')
        peca_qtds = request.form.getlist('peca_qtd[]')
        peca_valores = request.form.getlist('peca_valor[]')
        peca_prod_ids = request.form.getlist('peca_prod_id[]')

        total_pecas = 0.0
        for i, (nome, qtd_str, val_str) in enumerate(zip(peca_nomes, peca_qtds, peca_valores)):
            nome_clean = nome.strip()
            qtd = converter_valor(qtd_str) or 1.0
            val_unit = converter_valor(val_str)
            sub = round(qtd * val_unit, 2)
            if nome_clean:
                item = ItemOS(
                    os_id=os.id,
                    tipo='PECA',
                    descricao=nome_clean,
                    quantidade=qtd,
                    valor_unitario=val_unit,
                    subtotal=sub
                )
                # Vínculo com produto do catálogo e baixa no estoque
                if i < len(peca_prod_ids) and peca_prod_ids[i]:
                    try:
                        p_id = int(peca_prod_ids[i])
                        prod = Produto.query.filter_by(id=p_id, empresa_id=g.empresa.id).first()
                        if prod:
                            item.produto_id = prod.id
                            prod.estoque_atual = round(prod.estoque_atual - qtd, 2)
                            if not item.ncm and prod.ncm:
                                item.ncm = prod.ncm
                            if not item.cfop and prod.cfop:
                                item.cfop = prod.cfop
                    except (ValueError, TypeError):
                        pass

                db.session.add(item)
                total_pecas += sub

        # 2. Processar Serviços / Mão de Obra
        serv_nomes = request.form.getlist('servico_nome[]')
        serv_qtds = request.form.getlist('servico_qtd[]')
        serv_valores = request.form.getlist('servico_valor[]')
        serv_prod_ids = request.form.getlist('servico_prod_id[]')

        total_servicos = 0.0
        for i, (nome, qtd_str, val_str) in enumerate(zip(serv_nomes, serv_qtds, serv_valores)):
            nome_clean = nome.strip()
            qtd = converter_valor(qtd_str) or 1.0
            val_unit = converter_valor(val_str)
            sub = round(qtd * val_unit, 2)
            if nome_clean:
                item = ItemOS(
                    os_id=os.id,
                    tipo='SERVICO',
                    descricao=nome_clean,
                    quantidade=qtd,
                    valor_unitario=val_unit,
                    subtotal=sub
                )
                if i < len(serv_prod_ids) and serv_prod_ids[i]:
                    try:
                        s_id = int(serv_prod_ids[i])
                        serv_prod = Produto.query.filter_by(id=s_id, empresa_id=g.empresa.id).first()
                        if serv_prod:
                            item.produto_id = serv_prod.id
                            if not item.codigo_servico_municipal and serv_prod.codigo_servico_municipal:
                                item.codigo_servico_municipal = serv_prod.codigo_servico_municipal
                            if not item.aliquota_iss and serv_prod.aliquota_iss:
                                item.aliquota_iss = serv_prod.aliquota_iss
                    except (ValueError, TypeError):
                        pass

                db.session.add(item)
                total_servicos += sub

        # Suporte fallback caso os campos antigos de valor tenham sido preenchidos
        if total_pecas == 0:
            total_pecas = converter_valor(request.form.get('valor_pecas', '0'))
        if total_servicos == 0:
            total_servicos = converter_valor(request.form.get('valor_mao_obra', '0'))

        os.valor_pecas = round(total_pecas, 2)
        os.valor_mao_obra = round(total_servicos, 2)
        os.valor_total = round(total_pecas + total_servicos, 2)

        os.servico_executado = request.form.get('servico_executado', '').strip()
        os.forma_pagamento = request.form.get('forma_pagamento', 'Dinheiro').strip()
        os.status = 'CONCLUIDA'
        if os.etapa_andamento in ['RECEBIDO', 'DIAGNOSTICO', 'EM_EXECUCAO']:
            os.etapa_andamento = 'PRONTO'
        os.data_conclusao = date.today()

        # Configuração flexível da data de retorno preventivo
        opcao_retorno = request.form.get('opcao_retorno', '90').strip()
        data_retorno_manual = request.form.get('data_retorno_manual', '').strip()

        if data_retorno_manual:
            try:
                os.retorno_previsto = date.fromisoformat(data_retorno_manual)
            except (ValueError, TypeError):
                os.retorno_previsto = date.today() + timedelta(days=90)
        elif opcao_retorno == 'sem_retorno':
            os.retorno_previsto = None
        else:
            try:
                dias = int(opcao_retorno)
                os.retorno_previsto = date.today() + timedelta(days=dias)
            except (ValueError, TypeError):
                os.retorno_previsto = date.today() + timedelta(days=90)

        # Entrada no Livro Caixa com isolamento de empresa
        if os.valor_total > 0:
            caixa = Transacao(
                empresa_id=g.empresa.id,
                tipo='RECEITA',
                descricao=f"Recebimento OS #{os.id} - {os.cliente.nome}",
                valor=os.valor_total,
                forma_pagamento=os.forma_pagamento,
                data_movimento=date.today(),
                os_id=os.id
            )
            db.session.add(caixa)

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/os/<int:os_id>/reabrir', methods=['POST'])
@login_required
def reabrir_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    if os.status == 'CONCLUIDA':
        # 1. Estorna a transação financeira do livro caixa da empresa
        transacao = Transacao.query.filter_by(os_id=os.id, empresa_id=g.empresa.id).first()
        if transacao:
            db.session.delete(transacao)

        # 2. Devolve estoque dos itens vinculados a produtos
        for it in os.itens:
            if it.produto_id:
                prod = Produto.query.filter_by(id=it.produto_id, empresa_id=g.empresa.id).first()
                if prod:
                    prod.estoque_atual = round(prod.estoque_atual + (it.quantidade or 1.0), 2)

        # 3. Exclui os itens lançados para poder cadastrar a lista correta
        ItemOS.query.filter_by(os_id=os.id).delete()

        # 4. Retorna a OS para o estado ABERTA
        os.status = 'ABERTA'
        os.valor_pecas = 0.0
        os.valor_mao_obra = 0.0
        os.valor_total = 0.0
        os.forma_pagamento = None
        os.data_conclusao = None
        os.retorno_previsto = None

        db.session.commit()
        flash(f"A OS #{os.id:04d} foi reaberta para edição, itens de estoque devolvidos e o caixa estornado.", "success")

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/os/<int:os_id>/alterar-retorno', methods=['POST'])
@login_required
def alterar_retorno_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    data_manual = request.form.get('data_retorno', '').strip()
    opcao = request.form.get('opcao_retorno', '').strip()

    if data_manual:
        try:
            os.retorno_previsto = date.fromisoformat(data_manual)
            db.session.commit()
            flash(f"Data de retorno da OS #{os.id:04d} alterada para {os.retorno_previsto.strftime('%d/%m/%Y')}!", "success")
        except (ValueError, TypeError):
            flash("Data de retorno inválida.", "error")
    elif opcao == 'sem_retorno':
        os.retorno_previsto = None
        db.session.commit()
        flash(f"Lembrete de retorno da OS #{os.id:04d} removido.", "info")
    elif opcao:
        try:
            dias = int(opcao)
            base = os.data_conclusao or date.today()
            os.retorno_previsto = base + timedelta(days=dias)
            db.session.commit()
            flash(f"Data de retorno da OS #{os.id:04d} recalculada para {os.retorno_previsto.strftime('%d/%m/%Y')} (+{dias} dias)!", "success")
        except (ValueError, TypeError):
            flash("Opção de dias inválida.", "error")

    next_url = request.form.get('next') or request.referrer or url_for('ver_os', os_id=os.id)
    return redirect(next_url)


# --- ETAPA DE ANDAMENTO E RASTREAMENTO PÚBLICO EM TEMPO REAL ---

@app.route('/os/<int:os_id>/etapa', methods=['POST'])
@login_required
def alterar_etapa_os(os_id):
    os_obj = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    nova_etapa = request.form.get('etapa', '').strip().upper()
    etapas_validas = ['RECEBIDO', 'DIAGNOSTICO', 'EM_EXECUCAO', 'PRONTO', 'ENTREGUE']
    
    if nova_etapa in etapas_validas:
        os_obj.etapa_andamento = nova_etapa
        db.session.commit()
        flash(f"Etapa da OS #{os_obj.id:04d} atualizada para: {nova_etapa}", "success")
    else:
        flash("Etapa inválida.", "error")

    next_url = request.form.get('next') or request.referrer or url_for('ver_os', os_id=os_obj.id)
    return redirect(next_url)


@app.route('/status/<string:codigo_rastreio>')
def rastreio_publico_os(codigo_rastreio):
    """Página pública mobile-friendly para o cliente acompanhar o andamento em tempo real"""
    if not codigo_rastreio or len(codigo_rastreio) < 6:
        return render_template('404.html'), 404

    os_obj = OrdemServico.query.filter_by(codigo_rastreio=codigo_rastreio).first_or_404()
    empresa = os_obj.empresa
    return render_template('os_status_publico.html', os=os_obj, empresa=empresa)


# --- ASSINATURA DIGITAL DO CLIENTE NA TELA ---

@app.route('/os/<int:os_id>/salvar-assinatura', methods=['POST'])
@login_required
def salvar_assinatura_os(os_id):
    os_obj = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    
    if request.is_json:
        data = request.get_json() or {}
        assinatura_base64 = data.get('assinatura_base64', '').strip()
    else:
        assinatura_base64 = request.form.get('assinatura_base64', '').strip()
    
    if assinatura_base64 and assinatura_base64.startswith('data:image'):
        os_obj.assinatura_cliente_data = assinatura_base64
        os_obj.assinatura_data_hora = datetime.now()
        db.session.commit()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'sucesso': True, 'mensagem': 'Assinatura registrada com sucesso!'})
        flash("Assinatura digital do cliente coletada e salva com sucesso!", "success")
    else:
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum desenho de assinatura foi enviado.'}), 400
        flash("Nenhum desenho de assinatura foi enviado.", "warning")

    return redirect(url_for('ver_os', os_id=os_obj.id))


# --- IMPRESSÃO DE CUPOM TÉRMICO (BOBINA 80MM / 58MM) ---

@app.route('/os/<int:os_id>/cupom')
@login_required
def cupom_termico_os(os_id):
    os_obj = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    empresa = g.empresa
    return render_template('os_cupom.html', os=os_obj, empresa=empresa)


# --- CATÁLOGO DE PRODUTOS, PEÇAS & ESTOQUE ---

@app.route('/produtos')
@login_required
def lista_produtos():
    busca = request.args.get('busca', '').strip()
    tipo_filtro = request.args.get('tipo', '').strip().upper()
    alerta_estoque = request.args.get('alerta', '').strip()

    query = Produto.query.filter_by(empresa_id=g.empresa.id, ativo=True)
    if busca:
        query = query.filter(
            (Produto.nome.ilike(f'%{busca}%')) |
            (Produto.codigo.ilike(f'%{busca}%'))
        )
    if tipo_filtro in ['PECA', 'SERVICO']:
        query = query.filter_by(tipo=tipo_filtro)
    
    produtos = query.order_by(Produto.nome.asc()).all()

    if alerta_estoque == 'baixo':
        produtos = [p for p in produtos if p.tipo == 'PECA' and p.estoque_atual <= p.estoque_minimo]

    todos_itens = Produto.query.filter_by(empresa_id=g.empresa.id, ativo=True).all()
    total_pecas = sum(1 for p in todos_itens if p.tipo == 'PECA')
    total_servicos = sum(1 for p in todos_itens if p.tipo == 'SERVICO')
    baixo_estoque_count = sum(1 for p in todos_itens if p.tipo == 'PECA' and p.estoque_atual <= p.estoque_minimo)

    return render_template(
        'produtos.html',
        produtos=produtos,
        busca=busca,
        tipo_filtro=tipo_filtro,
        alerta_estoque=alerta_estoque,
        total_pecas=total_pecas,
        total_servicos=total_servicos,
        baixo_estoque_count=baixo_estoque_count
    )


@app.route('/produtos/novo', methods=['POST'])
@login_required
def novoProduto():
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash("O nome do item é obrigatório.", "error")
        return redirect(url_for('lista_produtos'))

    tipo = request.form.get('tipo', 'PECA').strip().upper()
    codigo = request.form.get('codigo', '').strip()
    unidade = request.form.get('unidade', 'UN').strip().upper()

    def converter_valor(v):
        try:
            return max(0.0, float(str(v or '0').replace('.', '').replace(',', '.').strip()))
        except (ValueError, TypeError):
            return 0.0

    preco_custo = converter_valor(request.form.get('preco_custo', '0'))
    preco_venda = converter_valor(request.form.get('preco_venda', '0'))
    estoque_atual = converter_valor(request.form.get('estoque_atual', '0')) if tipo == 'PECA' else 0.0
    estoque_minimo = converter_valor(request.form.get('estoque_minimo', '0')) if tipo == 'PECA' else 0.0

    ncm = re.sub(r'\D', '', request.form.get('ncm', '').strip())
    cfop = re.sub(r'\D', '', request.form.get('cfop', '').strip())
    cod_serv = request.form.get('codigo_servico_municipal', '').strip()
    aliq_iss = converter_valor(request.form.get('aliquota_iss', '0'))

    item = Produto(
        empresa_id=g.empresa.id,
        codigo=codigo,
        nome=nome,
        tipo=tipo,
        preco_custo=preco_custo,
        preco_venda=preco_venda,
        estoque_atual=estoque_atual,
        estoque_minimo=estoque_minimo,
        unidade=unidade,
        ncm=ncm,
        cfop=cfop,
        codigo_servico_municipal=cod_serv,
        aliquota_iss=aliq_iss,
        ativo=True
    )
    db.session.add(item)
    db.session.commit()
    flash(f"'{nome}' cadastrado com sucesso!", "success")
    return redirect(url_for('lista_produtos'))


@app.route('/produtos/<int:produto_id>/editar', methods=['POST'])
@login_required
def editar_produto(produto_id):
    prod = Produto.query.filter_by(id=produto_id, empresa_id=g.empresa.id).first_or_404()
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash("O nome é obrigatório.", "error")
        return redirect(url_for('lista_produtos'))

    def converter_valor(v):
        try:
            return max(0.0, float(str(v or '0').replace('.', '').replace(',', '.').strip()))
        except (ValueError, TypeError):
            return 0.0

    prod.nome = nome
    prod.codigo = request.form.get('codigo', '').strip()
    prod.tipo = request.form.get('tipo', prod.tipo).strip().upper()
    prod.unidade = request.form.get('unidade', prod.unidade).strip().upper()
    prod.preco_custo = converter_valor(request.form.get('preco_custo', prod.preco_custo))
    prod.preco_venda = converter_valor(request.form.get('preco_venda', prod.preco_venda))
    if prod.tipo == 'PECA':
        prod.estoque_atual = converter_valor(request.form.get('estoque_atual', prod.estoque_atual))
        prod.estoque_minimo = converter_valor(request.form.get('estoque_minimo', prod.estoque_minimo))
    prod.ncm = re.sub(r'\D', '', request.form.get('ncm', '').strip())
    prod.cfop = re.sub(r'\D', '', request.form.get('cfop', '').strip())
    prod.codigo_servico_municipal = request.form.get('codigo_servico_municipal', '').strip()
    prod.aliquota_iss = converter_valor(request.form.get('aliquota_iss', '0'))

    db.session.commit()
    flash(f"Item '{prod.nome}' atualizado com sucesso!", "success")
    return redirect(url_for('lista_produtos'))


@app.route('/produtos/<int:produto_id>/excluir', methods=['POST'])
@login_required
def excluir_produto(produto_id):
    prod = Produto.query.filter_by(id=produto_id, empresa_id=g.empresa.id).first_or_404()
    # Soft-delete para não quebrar integridade em ordens já existentes
    prod.ativo = False
    db.session.commit()
    flash(f"Item '{prod.nome}' removido do catálogo.", "info")
    return redirect(url_for('lista_produtos'))


@app.route('/api/produtos/busca')
@login_required
def api_busca_produtos():
    """Autocomplete rápido para lançamento de peças e serviços na OS"""
    termo = request.args.get('q', '').strip()
    tipo = request.args.get('tipo', '').strip().upper()

    query = Produto.query.filter_by(empresa_id=g.empresa.id, ativo=True)
    if termo:
        query = query.filter((Produto.nome.ilike(f'%{termo}%')) | (Produto.codigo.ilike(f'%{termo}%')))
    if tipo in ['PECA', 'SERVICO']:
        query = query.filter_by(tipo=tipo)

    resultados = query.limit(20).all()
    dados = []
    for p in resultados:
        dados.append({
            'id': p.id,
            'nome': p.nome,
            'codigo': p.codigo or '',
            'tipo': p.tipo,
            'preco_venda': float(p.preco_venda or 0.0),
            'estoque_atual': float(p.estoque_atual or 0.0),
            'unidade': p.unidade or 'UN',
            'ncm': p.ncm or '',
            'cfop': p.cfop or '',
            'codigo_servico_municipal': p.codigo_servico_municipal or '',
            'aliquota_iss': float(p.aliquota_iss or 0.0)
        })
    return jsonify(dados)


# --- FICHA DE CLIENTES E HISTÓRICO ISOLADO ---

@app.route('/clientes')
@login_required
def lista_clientes():
    busca = request.args.get('busca', '').strip()

    query = Cliente.query.filter_by(empresa_id=g.empresa.id)
    if busca:
        query = query.filter(
            (Cliente.nome.ilike(f'%{busca}%')) | 
            (Cliente.telefone.ilike(f'%{busca}%'))
        )

    clientes = query.order_by(Cliente.nome.asc()).all()
    return render_template('clientes.html', clientes=clientes, busca=busca)


@app.route('/clientes/<int:cliente_id>')
@login_required
def detalhe_cliente(cliente_id):
    cliente = Cliente.query.filter_by(id=cliente_id, empresa_id=g.empresa.id).first_or_404()

    historico_os = OrdemServico.query.filter_by(
        cliente_id=cliente.id,
        empresa_id=g.empresa.id
    ).order_by(OrdemServico.id.desc()).all()
    total_gasto = sum(os.valor_total for os in historico_os if os.status == 'CONCLUIDA')

    return render_template(
        'cliente_detalhe.html',
        cliente=cliente,
        historico_os=historico_os,
        total_gasto=total_gasto
    )


@app.route('/clientes/novo', methods=['POST'])
@login_required
def novo_cliente():
    nome = request.form.get('nome', '').strip()
    telefone = request.form.get('telefone', '').strip()
    cpf_cnpj = request.form.get('cpf_cnpj', '').strip()
    email = request.form.get('email', '').strip().lower()
    endereco = request.form.get('endereco', '').strip()
    numero = request.form.get('numero', '').strip()
    bairro = request.form.get('bairro', '').strip()
    cidade = request.form.get('cidade', '').strip()
    uf = request.form.get('uf', '').strip().upper()
    cep = request.form.get('cep', '').strip()

    veiculo_modelo = request.form.get('veiculo_modelo', '').strip()
    veiculo_placa = request.form.get('veiculo_placa', '').strip().upper()

    if not nome or not telefone:
        flash("Nome e telefone do cliente são obrigatórios.", "error")
        return redirect(url_for('lista_clientes'))

    # Verifica se já existe cliente com esse telefone na empresa
    cliente = Cliente.query.filter_by(empresa_id=g.empresa.id, telefone=telefone).first()
    if cliente:
        flash(f"Já existe um cliente cadastrado com o telefone '{telefone}': {cliente.nome}.", "warning")
        return redirect(url_for('detalhe_cliente', cliente_id=cliente.id))

    cliente = Cliente(
        empresa_id=g.empresa.id,
        nome=nome,
        telefone=telefone,
        cpf_cnpj=cpf_cnpj,
        email=email,
        endereco=endereco,
        numero=numero,
        bairro=bairro,
        cidade=cidade,
        uf=uf,
        cep=cep
    )
    db.session.add(cliente)
    db.session.flush()

    if veiculo_placa:
        veiculo = Veiculo(cliente_id=cliente.id, placa=veiculo_placa, modelo=veiculo_modelo or "Veículo")
        db.session.add(veiculo)

    db.session.commit()
    flash(f"Cliente '{cliente.nome}' cadastrado com sucesso!", "success")
    return redirect(url_for('detalhe_cliente', cliente_id=cliente.id))


@app.route('/clientes/<int:cliente_id>/editar', methods=['POST'])
@login_required
def editar_cliente(cliente_id):
    cliente = Cliente.query.filter_by(id=cliente_id, empresa_id=g.empresa.id).first_or_404()

    nome = request.form.get('nome', '').strip()
    telefone = request.form.get('telefone', '').strip()

    if not nome or not telefone:
        flash("Nome e telefone são obrigatórios.", "error")
        return redirect(request.referrer or url_for('detalhe_cliente', cliente_id=cliente.id))

    cliente.nome = nome
    cliente.telefone = telefone
    if 'cpf_cnpj' in request.form:
        cliente.cpf_cnpj = request.form.get('cpf_cnpj', '').strip()
    if 'email' in request.form:
        cliente.email = request.form.get('email', '').strip().lower()
    if 'endereco' in request.form:
        cliente.endereco = request.form.get('endereco', '').strip()
    if 'numero' in request.form:
        cliente.numero = request.form.get('numero', '').strip()
    if 'bairro' in request.form:
        cliente.bairro = request.form.get('bairro', '').strip()
    if 'cidade' in request.form:
        cliente.cidade = request.form.get('cidade', '').strip()
    if 'uf' in request.form:
        cliente.uf = request.form.get('uf', '').strip().upper()
    if 'cep' in request.form:
        cliente.cep = request.form.get('cep', '').strip()

    # Atualiza veículos existentes
    veiculo_ids = request.form.getlist('veiculo_id[]')
    veiculo_modelos = request.form.getlist('veiculo_modelo[]')
    veiculo_placas = request.form.getlist('veiculo_placa[]')

    for v_id_str, mod, plc in zip(veiculo_ids, veiculo_modelos, veiculo_placas):
        try:
            v_id = int(v_id_str)
            v = Veiculo.query.filter_by(id=v_id, cliente_id=cliente.id).first()
            if v:
                v.modelo = mod.strip() or v.modelo
                v.placa = plc.strip().upper() or v.placa
        except (ValueError, TypeError):
            continue

    # Adiciona novo veículo caso preenchido
    novo_mod = request.form.get('novo_veiculo_modelo', '').strip()
    novo_plc = request.form.get('novo_veiculo_placa', '').strip().upper()
    if novo_plc:
        novo_v = Veiculo(cliente_id=cliente.id, placa=novo_plc, modelo=novo_mod or "Veículo")
        db.session.add(novo_v)

    db.session.commit()
    flash(f"Cadastro de '{cliente.nome}' atualizado com sucesso!", "success")
    return redirect(request.referrer or url_for('detalhe_cliente', cliente_id=cliente.id))


# --- EXTRATO FINANCEIRO ISOLADO ---

@app.route('/financeiro')
@login_required
def relatorio_financeiro():
    hoje = date.today()
    mes_selecionado = int(request.args.get('mes', hoje.month))
    ano_selecionado = int(request.args.get('ano', hoje.year))

    transacoes = Transacao.query.filter(
        Transacao.empresa_id == g.empresa.id,
        db.extract('month', Transacao.data_movimento) == mes_selecionado,
        db.extract('year', Transacao.data_movimento) == ano_selecionado
    ).order_by(Transacao.data_movimento.desc(), Transacao.id.desc()).all()

    total_receitas = sum(t.valor for t in transacoes if t.tipo == 'RECEITA')
    total_despesas = sum(t.valor for t in transacoes if t.tipo == 'DESPESA')
    resultado_liquido = total_receitas - total_despesas

    totais_pagamento = {}
    for t in transacoes:
        if t.tipo == 'RECEITA':
            fp = t.forma_pagamento or 'Não Informado'
            totais_pagamento[fp] = round(totais_pagamento.get(fp, 0.0) + t.valor, 2)

    return render_template(
        'financeiro.html',
        transacoes=transacoes,
        total_receitas=total_receitas,
        total_despesas=total_despesas,
        resultado_liquido=resultado_liquido,
        totais_pagamento=totais_pagamento,
        mes_atual=mes_selecionado,
        ano_atual=ano_selecionado
    )


@app.route('/financeiro/exportar')
@login_required
def exportar_financeiro():
    hoje = date.today()
    mes = int(request.args.get('mes', hoje.month))
    ano = int(request.args.get('ano', hoje.year))

    transacoes = Transacao.query.filter(
        Transacao.empresa_id == g.empresa.id,
        db.extract('month', Transacao.data_movimento) == mes,
        db.extract('year', Transacao.data_movimento) == ano
    ).order_by(Transacao.data_movimento.asc()).all()

    si = StringIO()
    writer = csv.writer(si, delimiter=';')
    writer.writerow(['Data', 'Tipo', 'Descricao', 'Forma Pagamento', 'Valor (R$)', 'OS Origem'])

    for t in transacoes:
        writer.writerow([
            t.data_movimento.strftime('%d/%m/%Y'),
            t.tipo,
            t.descricao,
            t.forma_pagamento or '-',
            f"{t.valor:.2f}".replace('.', ','),
            f"#{t.os_id:04d}" if t.os_id else 'Manual'
        ])

    output = Response(si.getvalue(), mimetype='text/csv')
    output.headers["Content-Disposition"] = f"attachment; filename=extrato_{mes:02d}_{ano}.csv"
    return output


# --- RETORNOS PREVENTIVOS ISOLADOS ---

@app.route('/retornos')
@login_required
def retornos_preventivos():
    hoje = date.today()
    janela_aviso = hoje + timedelta(days=15)

    alertas = OrdemServico.query.filter(
        OrdemServico.empresa_id == g.empresa.id,
        OrdemServico.retorno_previsto.between(hoje, janela_aviso),
        OrdemServico.status == 'CONCLUIDA'
    ).order_by(OrdemServico.retorno_previsto.asc()).all()

    return render_template('retornos.html', alertas=alertas)


# --- BACKUP DIRETO ---

@app.route('/sistema/backup')
@login_required
def backup_banco():
    caminho_db = DB_PATH
    if not os.path.exists(caminho_db):
        caminho_db = os.path.join(BASE_DIR, 'instance', 'painel_gestao.db')
    if not os.path.exists(caminho_db):
        caminho_db = os.path.join(BASE_DIR, 'painel_gestao.db')

    data_str = date.today().strftime('%Y%m%d')
    return send_file(
        caminho_db,
        as_attachment=True,
        download_name=f"backup_painel_{data_str}.db"
    )


# --- CONFIGURAÇÕES DA EMPRESA (WHITE-LABEL) ---

@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes_empresa():
    empresa = g.empresa

    if request.method == 'POST':
        empresa.nome_empresa = request.form.get('nome_empresa', '').strip() or 'Minha Empresa'
        empresa.subtitulo = request.form.get('subtitulo', '').strip()
        empresa.cnpj_cpf = request.form.get('cnpj_cpf', '').strip()
        empresa.telefone = request.form.get('telefone', '').strip()
        empresa.whatsapp = request.form.get('whatsapp', '').strip()
        empresa.endereco = request.form.get('endereco', '').strip()
        empresa.cidade_uf = request.form.get('cidade_uf', '').strip()
        empresa.mensagem_rodape = request.form.get('mensagem_rodape', '').strip()

        tipo_negocio = request.form.get('tipo_negocio', '').strip().upper()
        if tipo_negocio in ['OFICINA', 'LOJA']:
            empresa.tipo_negocio = tipo_negocio

        # Atualização de e-mail e senha
        novo_email = request.form.get('email', '').strip().lower()
        if novo_email and novo_email != empresa.email:
            ja_existe = Empresa.query.filter_by(email=novo_email).first()
            if ja_existe:
                flash('Este e-mail já está sendo utilizado por outra empresa.', 'error')
            else:
                empresa.email = novo_email

        nova_senha = request.form.get('nova_senha', '').strip()
        if nova_senha:
            if len(nova_senha) < 6:
                flash('A nova senha deve ter no mínimo 6 dígitos.', 'error')
            else:
                empresa.senha_hash = generate_password_hash(nova_senha)
                flash('Senha atualizada com sucesso!', 'info')

        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename != '':
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
                if ext in ['png', 'jpg', 'jpeg', 'webp', 'svg']:
                    nome_arquivo = f"logo_{empresa.id}_{int(time.time())}.{ext}"
                    caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo)
                    file.save(caminho_salvar)
                    empresa.logo_filename = f"uploads/{nome_arquivo}"
                else:
                    flash('Formato de logo inválido! Envie imagem PNG, JPG, WEBP ou SVG.', 'error')

        db.session.commit()
        flash('Dados da empresa atualizados com sucesso!', 'success')
        return redirect(url_for('configuracoes_empresa'))

    return render_template('configuracoes.html', empresa=empresa)


@app.route('/configuracoes/remover-logo', methods=['POST'])
@login_required
def remover_logo_empresa():
    empresa = g.empresa
    empresa.logo_filename = None
    db.session.commit()
    flash('Logotipo removido com sucesso. O sistema exibirá o nome da empresa.', 'info')
    return redirect(url_for('configuracoes_empresa'))


# --- MÓDULO FISCAL & EMISSÃO DE NOTAS (FOCUS NFE) ---

@app.route('/configuracoes/fiscal', methods=['GET', 'POST'])
@login_required
def configuracoes_fiscal():
    empresa = g.empresa

    # Trava de Segurança SaaS: apenas empresas com o plano fiscal liberado pelo Dono Master podem acessar
    if not empresa.permite_emissao_fiscal and not empresa.is_admin:
        return render_template('fiscal_bloqueado.html', empresa=empresa)

    if request.method == 'POST':
        empresa.fiscal_ativo = (request.form.get('fiscal_ativo') == 'on')
        empresa.fiscal_ambiente = request.form.get('fiscal_ambiente', 'HOMOLOGACAO').strip().upper()
        empresa.fiscal_cnpj = re.sub(r'\D', '', request.form.get('fiscal_cnpj', ''))
        empresa.fiscal_razao_social = request.form.get('fiscal_razao_social', '').strip()
        empresa.fiscal_nome_fantasia = request.form.get('fiscal_nome_fantasia', '').strip()
        empresa.fiscal_inscricao_municipal = request.form.get('fiscal_inscricao_municipal', '').strip()
        empresa.fiscal_inscricao_estadual = request.form.get('fiscal_inscricao_estadual', '').strip()
        empresa.fiscal_codigo_municipio = re.sub(r'\D', '', request.form.get('fiscal_codigo_municipio', '5208707')) or '5208707'
        
        try:
            empresa.fiscal_regime_tributario = int(request.form.get('fiscal_regime_tributario', 1))
        except (ValueError, TypeError):
            empresa.fiscal_regime_tributario = 1

        empresa.fiscal_token_focus = request.form.get('fiscal_token_focus', '').strip()

        nova_senha_cert = request.form.get('fiscal_certificado_senha', '').strip()
        if nova_senha_cert:
            empresa.fiscal_certificado_senha = nova_senha_cert

        # Upload do Certificado Digital A1 (.pfx ou .p12)
        if 'certificado' in request.files:
            file = request.files['certificado']
            if file and file.filename != '':
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
                if ext in ['pfx', 'p12']:
                    cert_folder = os.path.join(INSTANCE_DIR, 'certificados')
                    os.makedirs(cert_folder, exist_ok=True)
                    cert_filename = f"cert_{empresa.id}_{int(time.time())}.{ext}"
                    caminho_cert = os.path.join(cert_folder, cert_filename)
                    file.save(caminho_cert)
                    empresa.fiscal_certificado_filename = cert_filename
                    flash("Certificado Digital A1 enviado com sucesso!", "info")
                else:
                    flash("Formato de certificado inválido! Envie um arquivo .pfx ou .p12.", "error")

        db.session.commit()
        flash("Configurações do Módulo Fiscal atualizadas com sucesso!", "success")
        return redirect(url_for('configuracoes_fiscal'))

    return render_template('configuracoes_fiscal.html', empresa=empresa)


@app.route('/configuracoes/fiscal/remover-certificado', methods=['POST'])
@login_required
def remover_certificado_fiscal():
    empresa = g.empresa
    if not empresa.permite_emissao_fiscal and not empresa.is_admin:
        return redirect(url_for('dashboard'))

    if empresa.fiscal_certificado_filename:
        caminho = os.path.join(INSTANCE_DIR, 'certificados', empresa.fiscal_certificado_filename)
        if os.path.exists(caminho):
            try:
                os.remove(caminho)
            except Exception:
                pass
        empresa.fiscal_certificado_filename = None
        empresa.fiscal_certificado_senha = ""
        db.session.commit()
        flash("Certificado Digital removido com sucesso.", "info")

    return redirect(url_for('configuracoes_fiscal'))


@app.route('/os/<int:os_id>/emitir-nfse', methods=['POST'])
@login_required
def emitir_nfse_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    empresa = g.empresa

    # Trava de Segurança SaaS
    if not empresa.permite_emissao_fiscal and not empresa.is_admin:
        flash("Sua empresa não possui o Módulo Fiscal contratado. Fale com o suporte para ativar.", "warning")
        return redirect(url_for('ver_os', os_id=os.id))

    if not empresa.fiscal_ativo or not empresa.fiscal_token_focus:
        flash("O Módulo Fiscal não está ativo ou o Token da Focus NFe não foi configurado nas Configurações Fiscais.", "error")
        return redirect(url_for('configuracoes_fiscal'))

    itens_servico = [it for it in os.itens if it.tipo == 'SERVICO']
    if not itens_servico:
        flash("Esta Ordem de Serviço não possui itens de serviço/mão de obra para emissão de NFS-e.", "warning")
        return redirect(url_for('ver_os', os_id=os.id))

    import focus_nfe
    sucesso, res = focus_nfe.autorizar_nfse(empresa, os, itens_servico)

    if sucesso:
        nova_nota = NotaFiscal(
            empresa_id=empresa.id,
            ordem_servico_id=os.id,
            tipo_nota='NFSE',
            referencia_uuid=res['referencia'],
            status=res.get('status', 'PROCESSANDO'),
            mensagem_sefaz=res.get('mensagem', ''),
            valor_total=sum(float(it.subtotal or 0.0) for it in itens_servico),
            data_emissao=date.today()
        )
        db.session.add(nova_nota)
        db.session.commit()
        flash("NFS-e enviada com sucesso para a Prefeitura! Status: " + res.get('status', 'PROCESSANDO'), "success")
    else:
        flash(f"Erro ao emitir NFS-e: {res}", "error")

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/os/<int:os_id>/emitir-nfce', methods=['POST'])
@login_required
def emitir_nfce_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    empresa = g.empresa

    if not empresa.permite_emissao_fiscal and not empresa.is_admin:
        flash("Sua empresa não possui o Módulo Fiscal contratado.", "warning")
        return redirect(url_for('ver_os', os_id=os.id))

    if not empresa.fiscal_ativo or not empresa.fiscal_token_focus:
        flash("O Módulo Fiscal não está ativo ou o Token da Focus NFe não foi configurado.", "error")
        return redirect(url_for('configuracoes_fiscal'))

    itens_pecas = [it for it in os.itens if it.tipo == 'PECA']
    if not itens_pecas:
        flash("Esta Ordem de Serviço não possui peças ou produtos para emissão de NFC-e.", "warning")
        return redirect(url_for('ver_os', os_id=os.id))

    import focus_nfe
    sucesso, res = focus_nfe.autorizar_nfce(empresa, os, itens_pecas)

    if sucesso:
        nova_nota = NotaFiscal(
            empresa_id=empresa.id,
            ordem_servico_id=os.id,
            tipo_nota='NFCE',
            referencia_uuid=res['referencia'],
            status=res.get('status', 'PROCESSANDO'),
            mensagem_sefaz=res.get('mensagem', ''),
            valor_total=sum(float(it.subtotal or 0.0) for it in itens_pecas),
            data_emissao=date.today()
        )
        db.session.add(nova_nota)
        db.session.commit()
        flash("NFC-e enviada com sucesso para a SEFAZ! Status: " + res.get('status', 'PROCESSANDO'), "success")
    else:
        flash(f"Erro ao emitir NFC-e: {res}", "error")

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/fiscal/nota/<int:nota_id>/consultar', methods=['POST'])
@login_required
def consultar_status_nota(nota_id):
    nota = NotaFiscal.query.filter_by(id=nota_id, empresa_id=g.empresa.id).first_or_404()
    import focus_nfe
    sucesso, res = focus_nfe.consultar_nfse(g.empresa, nota.referencia_uuid)

    if sucesso:
        nota.status = res.get('status', nota.status)
        if res.get('numero'):
            nota.numero_nota = str(res.get('numero'))
        if res.get('url_danfe'):
            nota.url_danfe_pdf = res.get('url_danfe')
        if res.get('url_xml'):
            nota.url_xml = res.get('url_xml')
        if res.get('mensagem'):
            nota.mensagem_sefaz = res.get('mensagem')
        db.session.commit()
        flash(f"Status da nota atualizado: {nota.status}", "info")
    else:
        flash(f"Erro ao consultar Focus NFe: {res}", "error")

    next_url = request.form.get('next') or request.referrer or url_for('ver_os', os_id=nota.ordem_servico_id)
    return redirect(next_url)


# --- CENTRAL DA CONTABILIDADE: EXPORTAÇÃO DE XMLs EM LOTE (.ZIP) ---

@app.route('/fiscal/exportar-mes')
@login_required
def fiscal_exportar_mes():
    empresa = g.empresa
    mes_param = request.args.get('mes', '').strip() # formato 'YYYY-MM'
    
    if not mes_param:
        hoje = date.today()
        mes_param = f"{hoje.year}-{hoje.month:02d}"

    try:
        ano, mes = [int(x) for x in mes_param.split('-')]
        data_ini = date(ano, mes, 1)
        if mes == 12:
            data_fim = date(ano + 1, 1, 1) - timedelta(days=1)
        else:
            data_fim = date(ano, mes + 1, 1) - timedelta(days=1)
    except Exception:
        flash("Formato de mês inválido. Use AAAA-MM.", "error")
        return redirect(url_for('configuracoes_fiscal'))

    notas = NotaFiscal.query.filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.data_emissao >= data_ini,
        NotaFiscal.data_emissao <= data_fim
    ).order_by(NotaFiscal.data_emissao.asc(), NotaFiscal.id.asc()).all()

    if not notas:
        flash(f"Nenhuma nota fiscal encontrada no período {data_ini.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}.", "warning")
        return redirect(url_for('configuracoes_fiscal'))

    import requests
    zip_buffer = BytesIO()
    csv_output = StringIO()
    csv_writer = csv.writer(csv_output, delimiter=';')
    csv_writer.writerow(['NUMERO', 'SERIE', 'TIPO', 'DATA_EMISSAO', 'VALOR_TOTAL', 'STATUS', 'REFERENCIA', 'CHAVE_ACESSO', 'URL_XML', 'URL_DANFE'])

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for n in notas:
            csv_writer.writerow([
                n.numero_nota or '',
                n.serie_nota or '',
                n.tipo_nota,
                n.data_emissao.strftime('%d/%m/%Y') if n.data_emissao else '',
                f"{float(n.valor_total or 0.0):.2f}".replace('.', ','),
                n.status,
                n.referencia_uuid,
                n.chave_acesso or '',
                n.url_xml or '',
                n.url_danfe_pdf or ''
            ])

            if n.url_xml and n.url_xml.startswith('http'):
                try:
                    r = requests.get(n.url_xml, timeout=10)
                    if r.status_code == 200 and r.content:
                        xml_name = f"xmls/{n.tipo_nota}_{n.numero_nota or n.referencia_uuid}.xml"
                        zip_file.writestr(xml_name, r.content)
                except Exception:
                    pass

        zip_file.writestr(f"relatorio_fiscal_{mes_param}.csv", csv_output.getvalue().encode('utf-8-sig'))

    zip_buffer.seek(0)
    nome_download = f"fechamento_fiscal_{empresa.nome_empresa.replace(' ', '_')}_{mes_param}.zip"
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=nome_download
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)