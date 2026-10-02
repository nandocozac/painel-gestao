from datetime import date
from database import db

class Empresa(db.Model):
    __tablename__ = 'empresas'
    
    id = db.Column(db.Integer, primary_key=True)
    nome_empresa = db.Column(db.String(150), nullable=False, default="Minha Empresa")
    subtitulo = db.Column(db.String(200), default="Serviços Especializados e Atendimento Profissional")
    cnpj_cpf = db.Column(db.String(30), default="")
    telefone = db.Column(db.String(30), default="")
    whatsapp = db.Column(db.String(30), default="")
    endereco = db.Column(db.String(200), default="")
    cidade_uf = db.Column(db.String(100), default="")
    logo_filename = db.Column(db.String(255), default="logo.png")
    mensagem_rodape = db.Column(db.String(255), default="Agradecemos a preferência! Volte sempre.")
    
    # Credenciais de Acesso
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    data_criacao = db.Column(db.Date, default=date.today)
    
    # Controle de Acesso / Assinaturas (SaaS)
    is_admin = db.Column(db.Boolean, default=False)
    status_assinatura = db.Column(db.String(20), default='PENDENTE') # 'ATIVO', 'PENDENTE', 'BLOQUEADO'
    data_validade = db.Column(db.Date, nullable=True)
    observacoes_admin = db.Column(db.Text, default="")
    
    # Dados de cobrança (definidos pelo Dono Master para receber dos clientes)
    chave_pix = db.Column(db.String(100), default="nandocozac@gmail.com")
    titular_pix = db.Column(db.String(100), default="Fernando Cozac")
    valor_mensalidade = db.Column(db.Float, default=29.90)
    valor_anual = db.Column(db.Float, default=249.90)

    # Relacionamentos isolados por empresa
    clientes = db.relationship('Cliente', backref='empresa', lazy=True)
    ordens_servico = db.relationship('OrdemServico', backref='empresa', lazy=True)
    transacoes = db.relationship('Transacao', backref='empresa', lazy=True)


class Cliente(db.Model):
    __tablename__ = 'clientes'
    
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    nome = db.Column(db.String(120), nullable=False)
    telefone = db.Column(db.String(30), nullable=False)
    
    veiculos = db.relationship('Veiculo', backref='cliente', lazy=True)
    ordens_servico = db.relationship('OrdemServico', backref='cliente', lazy=True)


class Veiculo(db.Model):
    __tablename__ = 'veiculos'
    
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    placa = db.Column(db.String(20), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    
    ordens_servico = db.relationship('OrdemServico', backref='veiculo', lazy=True)


class OrdemServico(db.Model):
    __tablename__ = 'ordens_servico'
    
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculos.id'), nullable=True)
    
    descricao_problema = db.Column(db.Text, nullable=False)
    servico_executado = db.Column(db.Text, nullable=True)
    
    valor_pecas = db.Column(db.Float, default=0.0)
    valor_mao_obra = db.Column(db.Float, default=0.0)
    valor_total = db.Column(db.Float, default=0.0)
    forma_pagamento = db.Column(db.String(30), nullable=True)
    
    status = db.Column(db.String(20), default='ABERTA') # 'ABERTA' ou 'CONCLUIDA'
    data_abertura = db.Column(db.Date, default=date.today)
    data_conclusao = db.Column(db.Date, nullable=True)
    retorno_previsto = db.Column(db.Date, nullable=True)

    itens = db.relationship('ItemOS', backref='ordem_servico', cascade="all, delete-orphan", lazy=True)


class ItemOS(db.Model):
    __tablename__ = 'itens_os'
    
    id = db.Column(db.Integer, primary_key=True)
    os_id = db.Column(db.Integer, db.ForeignKey('ordens_servico.id'), nullable=False)
    tipo = db.Column(db.String(10), nullable=False) # 'PECA' ou 'SERVICO'
    descricao = db.Column(db.String(150), nullable=False)
    quantidade = db.Column(db.Float, default=1.0)
    valor_unitario = db.Column(db.Float, default=0.0)
    subtotal = db.Column(db.Float, default=0.0)


class Transacao(db.Model):
    __tablename__ = 'transacoes'
    
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    tipo = db.Column(db.String(10), nullable=False) # 'RECEITA' ou 'DESPESA'
    descricao = db.Column(db.String(200), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    forma_pagamento = db.Column(db.String(30), nullable=True)
    data_movimento = db.Column(db.Date, default=date.today)
    
    os_id = db.Column(db.Integer, db.ForeignKey('ordens_servico.id'), nullable=True)