from flask import Flask, redirect, url_for, session
from authlib.integrations.flask_client import OAuth
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Use a secure random key in production
oauth = OAuth(app)

oauth.register(
  name='oidc',
  authority='https://cognito-idp.us-east-2.amazonaws.com/us-east-2_z2nCjFM0V',
  client_id='uv9k4p9l4js5ud1i7dotiqo43',
  client_secret='<client secret>',
  server_metadata_url='https://cognito-idp.us-east-2.amazonaws.com/us-east-2_z2nCjFM0V/.well-known/openid-configuration',
  client_kwargs={'scope': 'phone openid email'}
)