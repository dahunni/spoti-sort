import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os

scope = "user-library-modify, playlist-modify-private, playlist-modify-public"
authi = SpotifyOAuth(cache_path='/config/tokens.txt', show_dialog=True, scope=scope, client_id=os.environ.get('CLIENT_ID'), client_secret=os.environ.get('CLIENT_SECRET'), redirect_uri='https://localhost')
sp = spotipy.Spotify(auth_manager=authi)
# playlists to monitor / to sort by date added


playlistenv = os.environ.get('PLAYLIST_IDS')
playlists = playlistenv.split(", ")

def getalltracks(playlistid):
    results = sp.playlist_items(playlistid)
    tracks = results['items']
    while results['next']:
        results = sp.next(results)
        tracks.extend(results['items'])

    return tracks


def listtracks(list):
    for idx, track in enumerate(list):
        print(idx, track)


def findintracklist(tracklist, id):
    for idx, track in enumerate(tracklist):
        if track['track']['id'] == id:
            return (idx)



def orderlistbydate(playlist, runonce=False, tracklist='', targetlist=''):
    if runonce != True:
        tracklist = getalltracks(playlist)
        targetlist = sorted(tracklist, key=lambda x: x['added_at'], reverse=True)
    for idx, items in enumerate(targetlist):
        ist = int(findintracklist(tracklist, items['track']['id']))
        soll = int(idx)

        if soll != ist:
            print('ist != soll')
            print(len(tracklist), len(targetlist))
            print(str(ist) + '>>>' + str(soll))
            print(tracklist[ist]['track']['name'], targetlist[soll]['track']['name'])
            tracklist.insert(soll, tracklist.pop(ist))
            results2 = sp.playlist_reorder_items(playlist, ist, soll, range_length=1)
            print(results2)
            print('MOVED')
            print('_______')

            orderlistbydate(playlist, runonce=True, tracklist=tracklist, targetlist=targetlist)
    return ('Ordered Playlist', playlist)


for playlist in playlists:
    print(orderlistbydate(playlist))

exit(0)
