class Config:
    SECRET_KEY = 'tu_clave_secreta_aqui'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///my_cinema_app.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAIL_SERVER = 'smtp.example.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = 'tu_correo@example.com'
    MAIL_PASSWORD = 'tu_contraseña'
    MAIL_DEFAULT_SENDER = 'tu_correo@example.com'