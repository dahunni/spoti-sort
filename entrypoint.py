import os
import sys
keep = True
crondonce = False
try:  
   os.environ["CLIENT_ID"]
except KeyError: 
   print("Please set the variable CLIENT_ID")
   keep = False
try:  
   os.environ["CLIENT_SECRET"]
except KeyError: 
   print("Please set the variable CLIENT_SECRET")
   keep = False
try:  
   os.environ["PLAYLIST_IDS"]
except KeyError: 
   print("Please set the variable PLAYLIST_IDS")
   keep = False
if keep == True:
    if os.path.isfile('/config/tokens.txt'):
        print ("tokens found. To reset the tokens, delete the /config/tokens.txt and rerun this container")
        os.system('crond -f')
        crondonce = True
    else:
        print ("You are running this Docker Container for the First time or did not mount the /data directory. Please open the docker containers shell (docker exec -it DOCKERID) and type >python firstrun.py<")
    sys.stdout.flush()

if keep != True:
    print("exiting")

while keep:
    keep = True
    if crondonce != True:
        if os.path.isfile('/config/tokens.txt'):
            os.system('crond -f')
            crondonce = True
