from datetime import date, datetime, timedelta
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
import os
import requests
from werkzeug.utils import secure_filename


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///luxury_wheels.db'
app.config['SECRET_KEY'] = 'a_tua_chave_secreta_aqui'
db = SQLAlchemy(app)


# Garante que a pasta de uploads existe
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
  os.makedirs(UPLOAD_FOLDER)


# --- MODELOS DA BASE DE DADOS ---

class Cliente(db.Model):
  """Modelo para representar os clientes e utilizadores do sistema."""
  __tablename__ = 'cliente'
  id = db.Column(db.Integer, primary_key=True)
  nome = db.Column(db.String(100), nullable=False)
  email = db.Column(db.String(100), unique=True, nullable=False)
  password = db.Column(db.String(200), nullable=False)
  papel = db.Column(db.String(20), default='cliente') # 'cliente', 'operador', 'admin'


class Veiculo(db.Model):
  """Modelo para representar os carros e motos disponíveis na frota."""
  id = db.Column(db.Integer, primary_key=True)
  marca = db.Column(db.String(50), nullable=False)
  modelo = db.Column(db.String(50), nullable=False)
  categoria = db.Column(db.String(50), nullable=False)
  tipo = db.Column(db.String(20), nullable=False) # 'Carro' ou 'Moto'
  combustivel = db.Column(
    db.String(30), nullable=False, default='Gasolina'
  )  # 'Gasolina', 'Gasóleo', 'Elétrico', 'Híbrido'
  capacidade = db.Column(db.Integer, nullable=False)
  imagem_url = db.Column(db.String(255), nullable=False)
  valor_diaria = db.Column(db.Float, nullable=False)
  data_ultima_revisao = db.Column(db.Date, nullable=False)
  data_proxima_revisao = db.Column(db.Date, nullable=False)
  data_ultima_legalizacao = db.Column(db.Date, nullable=False)
  disponivel = db.Column(db.Boolean, default=True)
  destaque = db.Column(db.Boolean, default=False)


class Reserva(db.Model):
  """Modelo para gerir os alugueres e reservas efetuados pelos clientes."""
  id = db.Column(db.Integer, primary_key=True)
  cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
  veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
  data_inicio = db.Column(db.Date, nullable=False)
  data_fim = db.Column(db.Date, nullable=False)
  valor_total = db.Column(db.Float, nullable=False)
  forma_pagamento = db.Column(db.String(50), nullable=False)  # Corrigido para aceitar texto direto
  estado = db.Column(db.String(20), default='Ativa') # 'Ativa', 'Cancelada'

  cliente = db.relationship('Cliente', backref=db.backref('reservas', lazy=True))
  veiculo = db.relationship('Veiculo', backref=db.backref('reservas', lazy=True))


# Criar tabelas e admin padrão automaticamente
with app.app_context():
  db.create_all()
  if not Cliente.query.filter_by(papel='admin').first():
    admin_inicial = Cliente(
        nome='Administrador',
        email='admin@luxurywheels.com',
        password='admin123',
        papel='admin',
    )
    db.session.add(admin_inicial)
    db.session.commit()


# --- ROTAS PRINCIPAIS ---

@app.route('/')
def index():
  """Página inicial que exibe os veículos em destaque na plataforma."""
  veiculos_destaque = Veiculo.query.filter_by(destaque=True, disponivel=True).all()
  if not veiculos_destaque:
    veiculos_destaque = Veiculo.query.filter_by(disponivel=True).limit(5).all()
  return render_template('index.html', veiculos=veiculos_destaque)


@app.route('/pesquisa')
def pesquisa():
  """Permite filtrar e procurar veículos por marca, modelo, categoria, tipo, combustível, preço e capacidade."""
  marca = request.args.get('marca', '')
  modelo = request.args.get('modelo', '')
  categoria = request.args.get('categoria', '')
  tipo = request.args.get('tipo', '')
  combustivel = request.args.get('combustivel', '')
  max_valor = request.args.get('valor_diaria', type=float)
  pessoas = request.args.get('quantidade_pessoas', type=int)

  if session.get('papel') in ['admin', 'operador']:
    query = Veiculo.query
  else:
    query = Veiculo.query.filter_by(disponivel=True)

  if marca:
    query = query.filter(Veiculo.marca.ilike(f'%{marca}%'))
  if modelo:
    query = query.filter(Veiculo.modelo.ilike(f'%{modelo}%'))
  if categoria:
    query = query.filter_by(categoria=categoria)
  if tipo:
    query = query.filter_by(tipo=tipo)
  if combustivel:
    query = query.filter_by(combustivel=combustivel)
  if max_valor:
    query = query.filter(Veiculo.valor_diaria <= max_valor)
  if pessoas:
    query = query.filter(Veiculo.capacidade == pessoas)

  veiculos = query.all()

  hoje = datetime.now().date()
  limite_legalizacao = hoje - timedelta(days=365)

  veiculos_com_estado = []
  for v in veiculos:
    revisao_valida = v.data_proxima_revisao >= hoje
    legalizacao_valida = v.data_ultima_legalizacao >= limite_legalizacao
    apto = revisao_valida and legalizacao_valida

    if session.get('papel') in ['admin', 'operador']:
      veiculos_com_estado.append({'veiculo': v, 'apto': apto})
    else:
      if apto and v.disponivel:
        veiculos_com_estado.append({'veiculo': v, 'apto': True})

  return render_template('pesquisa.html', veiculos_com_estado=veiculos_com_estado)


@app.route('/reservar/<int:veiculo_id>', methods=['GET', 'POST'])
def reservar(veiculo_id):
  """Processa a reserva de um veículo escolhido pelo cliente, validando datas e calculando o valor total."""
  if 'cliente_id' not in session:
    flash('Tens de iniciar sessão para efetuar uma reserva.', 'danger')
    return redirect(url_for('login'))

  veiculo = Veiculo.query.get_or_404(veiculo_id)
  hoje_str = datetime.now().strftime('%Y-%m-%d')

  if request.method == 'POST':
    data_inicio = datetime.strptime(request.form['data_inicio'], '%Y-%m-%d').date()
    data_fim = datetime.strptime(request.form['data_fim'], '%Y-%m-%d').date()
    hoje_date = datetime.now().date()

    if data_inicio < hoje_date:
      flash('Não é possível efetuar reservas com data anterior a hoje.', 'danger')
      return redirect(url_for('reservar', veiculo_id=veiculo_id))

    quantidade_dias = (data_fim - data_inicio).days
    if quantidade_dias <= 0:
      flash('A data de fim deve ser posterior à data de início.', 'danger')
      return redirect(url_for('reservar', veiculo_id=veiculo_id))

    valor_total = quantidade_dias * veiculo.valor_diaria
    forma_pagamento = request.form['forma_pagamento']

    nova_reserva = Reserva(
        cliente_id=session['cliente_id'],
        veiculo_id=veiculo.id,
        data_inicio=data_inicio,
        data_fim=data_fim,
        valor_total=valor_total,
        forma_pagamento=forma_pagamento,
        estado='Ativa',
    )

    veiculo.disponivel = False
    db.session.add(nova_reserva)
    db.session.commit()

    flash('Reserva concluída com sucesso!', 'success')
    return redirect(url_for('minhas_reservas'))

  return render_template('reservar.html', veiculo=veiculo, hoje=hoje_str)


@app.route('/reserva/editar/<int:reserva_id>', methods=['GET', 'POST'])
def editar_reserva(reserva_id):
  """Permite ao cliente alterar as datas da reserva ou proceder ao seu cancelamento total."""
  reserva = Reserva.query.get_or_404(reserva_id)

  if request.method == 'POST':
    acao = request.form.get('acao')

    if acao == 'cancelar':
      reserva.estado = 'Cancelada'
      veiculo = Veiculo.query.get(reserva.veiculo_id)
      if veiculo:
        veiculo.disponivel = True
      db.session.commit()
      flash('Reserva cancelada com sucesso.', 'info')
      return redirect(url_for('minhas_reservas'))

    elif acao == 'alterar':
      nova_inicio = datetime.strptime(request.form['data_inicio'], '%Y-%m-%d').date()
      nova_fim = datetime.strptime(request.form['data_fim'], '%Y-%m-%d').date()

      quantidade_dias = (nova_fim - nova_inicio).days
      if quantidade_dias <= 0:
        flash('Datas inválidas.', 'danger')
        return redirect(url_for('editar_reserva', reserva_id=reserva_id))

      veiculo = Veiculo.query.get(reserva.veiculo_id)
      reserva.data_inicio = nova_inicio
      reserva.data_fim = nova_fim
      reserva.valor_total = quantidade_dias * veiculo.valor_diaria

      db.session.commit()
      flash('Datas da reserva alteradas com sucesso!', 'success')
      return redirect(url_for('minhas_reservas'))

  return render_template('editar_reserva.html', reserva=reserva)


@app.route('/adicionar-veiculo', methods=['GET', 'POST'])
def adicionar_veiculo():
  """Permite a administradores e operadores registar novos veículos na frota com respetivas imagens e datas."""
  if 'papel' not in session or session['papel'] not in ['admin', 'operador']:
    flash('Acesso restrito a administradores e operadores.', 'danger')
    return redirect(url_for('index'))

  if request.method == 'POST':
    marca = request.form['marca']
    modelo = request.form['modelo']
    categoria = request.form['categoria']
    tipo = request.form['tipo']
    combustivel = request.form.get('combustivel', 'Gasolina')
    capacidade = int(request.form['capacidade'])
    valor_diaria = float(request.form['valor_diaria'])

    data_ultima_revisao = datetime.strptime(request.form['data_ultima_revisao'], '%Y-%m-%d').date()
    data_proxima_revisao = datetime.strptime(request.form['data_proxima_revisao'], '%Y-%m-%d').date()
    data_ultima_legalizacao = datetime.strptime(request.form['data_ultima_legalizacao'], '%Y-%m-%d').date()

    imagem_url_final = ''

    file = request.files.get('imagem_file')
    if file and file.filename != '':
      filename = secure_filename(file.filename)
      caminho_completo = os.path.join(app.config['UPLOAD_FOLDER'], filename)
      file.save(caminho_completo)
      imagem_url_final = f'uploads/{filename}'
    else:
      url_digitado = request.form.get('imagem_url_externa', '')
      if url_digitado:
        try:
          resposta = requests.get(url_digitado, timeout=10)
          if resposta.status_code == 200:
            nome_ficheiro = secure_filename(f'{marca}_{modelo}.jpg').lower()
            caminho_completo = os.path.join(app.config['UPLOAD_FOLDER'], nome_ficheiro)
            with open(caminho_completo, 'wb') as f:
              f.write(resposta.content)
            imagem_url_final = f'uploads/{nome_ficheiro}'
          else:
            flash('Aviso: Não foi possível descarregar a imagem do URL, mas o veículo será registado.', 'warning')
        except Exception:
          flash('Aviso: Erro ao processar o URL da imagem.', 'warning')

    if not imagem_url_final:
      imagem_url_final = 'uploads/default.jpg'

    novo_veiculo = Veiculo(
        marca=marca,
        modelo=modelo,
        categoria=categoria,
        tipo=tipo,
        combustivel=combustivel,
        capacidade=capacidade,
        imagem_url=imagem_url_final,
        valor_diaria=valor_diaria,
        data_ultima_revisao=data_ultima_revisao,
        data_proxima_revisao=data_proxima_revisao,
        data_ultima_legalizacao=data_ultima_legalizacao,
        disponivel=True,
    )

    db.session.add(novo_veiculo)
    db.session.commit()

    flash('Veículo registado com sucesso!', 'success')
    return redirect(url_for('pesquisa'))

  return render_template('adicionar_veiculo.html')


@app.route('/registo', methods=['GET', 'POST'])
def registo():
  """Processa o registo de novos clientes no website."""
  if request.method == 'POST':
    nome = request.form['nome']
    email = request.form['email']
    password = request.form['password']

    utilizador_existente = Cliente.query.filter_by(email=email).first()
    if utilizador_existente:
      flash('Este email já está registado.', 'danger')
      return redirect(url_for('registo'))

    novo_cliente = Cliente(
        nome=nome,
        email=email,
        password=password,
        papel='cliente',
    )
    db.session.add(novo_cliente)
    db.session.commit()

    flash('Registo efetuado com sucesso! Podes iniciar sessão.', 'success')
    return redirect(url_for('login'))

  return render_template('registo.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
  """Autentica o utilizador e cria a sessão consoante o seu papel (cliente, operador ou admin)."""
  if request.method == 'POST':
    email = request.form['email']
    password = request.form['password']

    utilizador = Cliente.query.filter_by(email=email, password=password).first()

    if utilizador:
      session['cliente_id'] = utilizador.id
      session['nome'] = utilizador.nome
      session['papel'] = utilizador.papel
      session['email'] = utilizador.email

      flash(f'Bem-vindo de volta, {utilizador.nome}!', 'success')

      if utilizador.papel == 'admin':
        return redirect(url_for('painel_admin'))
      else:
        return redirect(url_for('pesquisa'))
    else:
      flash('Email ou palavra-passe incorretos.', 'danger')

  return render_template('login.html')


@app.route('/logout')
def logout():
  """Termina a sessão ativa do utilizador e redireciona para a página inicial."""
  session.clear()
  flash('Sessão terminada com sucesso.', 'info')
  return redirect(url_for('index'))


# --- PAINEL DO ADMINISTRADOR E GESTÃO DE OPERADORES ---

@app.route('/admin')
def painel_admin():
  """Exibe o painel de controlo exclusivo para administradores gerirem operadores e contas."""
  if session.get('papel') != 'admin':
    flash('Acesso exclusivo a administradores.', 'danger')
    return redirect(url_for('index'))

  utilizador_atual = Cliente.query.get(session['cliente_id'])
  if utilizador_atual:
    session['email'] = utilizador_atual.email

  utilizadores = Cliente.query.filter(Cliente.papel.in_(['operador', 'admin'])).all()
  return render_template('painel_admin.html', utilizadores=utilizadores)


@app.route('/criar-operador', methods=['POST'])
def criar_operador():
  """Permite ao administrador criar novas contas de operadores ou administradores."""
  if session.get('papel') != 'admin':
    flash('Permissão negada.', 'danger')
    return redirect(url_for('index'))

  nome = request.form['nome']
  email = request.form['email']
  password = request.form['password']
  papel_pretendido = request.form['papel']

  if papel_pretendido == 'admin' and session.get('email') != 'admin@luxurywheels.com':
    flash('Apenas o Administrador Principal tem autorização para criar novos Administradores.', 'danger')
    return redirect(url_for('painel_admin'))

  if Cliente.query.filter_by(email=email).first():
    flash('Já existe uma conta registada com esse email.', 'warning')
    return redirect(url_for('painel_admin'))

  novo_u = Cliente(
      nome=nome, email=email, password=password, papel=papel_pretendido
  )
  db.session.add(novo_u)
  db.session.commit()

  flash(f'Conta de {papel_pretendido.capitalize()} criada com sucesso!', 'success')
  return redirect(url_for('painel_admin'))


@app.route('/eliminar-utilizador/<int:id>')
def eliminar_utilizador(id):
  """Permite ao administrador eliminar contas de utilizadores (com exceção do admin principal)."""
  if session.get('papel') != 'admin':
    flash('Acesso negado.', 'danger')
    return redirect(url_for('index'))

  u = Cliente.query.get_or_404(id)

  if u.email == 'admin@luxurywheels.com' or u.id == session.get('cliente_id'):
    flash('Não é possível eliminar o Administrador Principal ou a sua própria conta.', 'danger')
    return redirect(url_for('painel_admin'))

  if u.papel == 'admin' and session.get('email') != 'admin@luxurywheels.com':
    flash('Não tem permissão para eliminar outros Administradores.', 'danger')
    return redirect(url_for('painel_admin'))

  db.session.delete(u)
  db.session.commit()
  flash('Utilizador eliminado com sucesso!', 'success')
  return redirect(url_for('painel_admin'))


@app.route('/editar-utilizador/<int:id>', methods=['GET', 'POST'])
def editar_utilizador(id):
  """Permite ao administrador atualizar os dados de um utilizador específico."""
  if session.get('papel') != 'admin':
    flash('Acesso negado.', 'danger')
    return redirect(url_for('index'))

  u = Cliente.query.get_or_404(id)

  if u.papel == 'admin' and session.get('email') != 'admin@luxurywheels.com':
    flash('Não tem permissão para editar outros Administradores.', 'danger')
    return redirect(url_for('painel_admin'))

  if request.method == 'POST':
    u.nome = request.form['nome']
    u.email = request.form['email']
    if request.form.get('password'):
      u.password = request.form['password']
    db.session.commit()
    flash('Dados do utilizador atualizados com sucesso!', 'success')
    return redirect(url_for('painel_admin'))

  return render_template('editar_utilizador.html', u=u)


# --- GESTÃO DE RESERVAS E FROTA ---

@app.route('/minhas-reservas')
def minhas_reservas():
  """Lista as reservas ativas e o histórico associado ao cliente autenticado (ou todas para administradores)."""
  if 'cliente_id' not in session:
    flash('Tens de iniciar sessão para ver as reservas.', 'warning')
    return redirect(url_for('login'))

  if session.get('papel') in ['admin', 'operador']:
    reservas_ativas = Reserva.query.filter_by(estado='Ativa').all()
    reservas_historico = Reserva.query.filter(Reserva.estado != 'Ativa').all()
  else:
    reservas_ativas = Reserva.query.filter_by(cliente_id=session['cliente_id'], estado='Ativa').all()
    reservas_historico = Reserva.query.filter(Reserva.cliente_id == session['cliente_id'], Reserva.estado != 'Ativa').all()

  return render_template(
      'minhas_reservas.html',
      reservas_ativas=reservas_ativas,
      reservas_historico=reservas_historico,
  )


@app.route('/toggle-destaque/<int:veiculo_id>')
def toggle_destaque(veiculo_id):
  """Alterna o estado de destaque de um veículo na página inicial (exclusivo para admin/operador)."""
  if session.get('papel') not in ['admin', 'operador']:
    flash('Acesso restrito a administradores e operadores.', 'danger')
    return redirect(url_for('index'))

  veiculo = Veiculo.query.get_or_404(veiculo_id)
  veiculo.destaque = not veiculo.destaque
  db.session.commit()

  estado_msg = 'colocado em destaque' if veiculo.destaque else 'removido dos destaques'
  flash(f'Veículo {veiculo.marca} {veiculo.modelo} {estado_msg}!', 'success')

  return redirect(url_for('pesquisa'))


@app.route('/editar-veiculo/<int:veiculo_id>', methods=['GET', 'POST'])
def editar_veiculo(veiculo_id):
  """Permite atualizar as informações, datas de revisão/legalização e imagens de um veículo existente."""
  if session.get('papel') not in ['admin', 'operador']:
    flash('Acesso restrito a administradores e operadores.', 'danger')
    return redirect(url_for('index'))

  veiculo = Veiculo.query.get_or_404(veiculo_id)

  if request.method == 'POST':
    veiculo.marca = request.form['marca']
    veiculo.modelo = request.form['modelo']
    veiculo.categoria = request.form['categoria']
    veiculo.tipo = request.form['tipo']
    veiculo.combustivel = request.form.get('combustivel', veiculo.combustivel)
    veiculo.capacidade = int(request.form['capacidade'])
    veiculo.valor_diaria = float(request.form['valor_diaria'])

    veiculo.data_ultima_revisao = datetime.strptime(
        request.form['data_ultima_revisao'], '%Y-%m-%d'
    ).date()
    veiculo.data_proxima_revisao = datetime.strptime(
        request.form['data_proxima_revisao'], '%Y-%m-%d'
    ).date()
    veiculo.data_ultima_legalizacao = datetime.strptime(
        request.form['data_ultima_legalizacao'], '%Y-%m-%d'
    ).date()

    file = request.files.get('imagem_file')
    if file and file.filename != '':
      filename = secure_filename(file.filename)
      caminho_completo = os.path.join(app.config['UPLOAD_FOLDER'], filename)
      file.save(caminho_completo)
      veiculo.imagem_url = f'uploads/{filename}'
    else:
      url_digitado = request.form.get('imagem_url_externa', '')
      if url_digitado:
        try:
          resposta = requests.get(url_digitado, timeout=10)
          if resposta.status_code == 200:
            nome_ficheiro = secure_filename(
                f'{veiculo.marca}_{veiculo.modelo}.jpg'
            ).lower()
            caminho_completo = os.path.join(
                app.config['UPLOAD_FOLDER'], nome_ficheiro
            )
            with open(caminho_completo, 'wb') as f:
              f.write(resposta.content)
            veiculo.imagem_url = f'uploads/{nome_ficheiro}'
        except Exception:
          flash(
              'Erro ao descarregar a nova imagem. Imagem anterior mantida.',
              'warning',
          )

    db.session.commit()
    flash(
        f'Veículo {veiculo.marca} {veiculo.modelo} atualizado com sucesso!',
        'success',
    )
    return redirect(url_for('pesquisa'))

  return render_template('editar_veiculo.html', veiculo=veiculo)


if __name__ == '__main__':
  app.run(debug=True)