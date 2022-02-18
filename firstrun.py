import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os

url = ("https://accounts.spotify.com/en/authorize?client_id=%CLIENTID%&response_type=code&redirect_uri=https%3A%2F%2Flocalhost&scope=+playlist-modify-private++playlist-modify-public+user-library-modify&show_dialog=True")
url = url.replace("%CLIENTID%", os.environ.get('CLIENT_ID'))

print("please open the following URL:")
print(url)



scope = "user-library-modify, playlist-modify-private, playlist-modify-public"
authi = SpotifyOAuth(cache_path='/config/tokens.txt', show_dialog=True, scope=scope, client_id=os.environ.get('CLIENT_ID'), client_secret=os.environ.get('CLIENT_SECRET'), redirect_uri='https://localhost')
sp = spotipy.Spotify(auth_manager=authi)
sp.me()
print("Setup done, starting cron.....")
os.system('crond -b')