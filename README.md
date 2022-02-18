<p align="center">
  <a href="" rel="noopener">
</a>
</p>

<h3 align="center">Spoti-sort</h3>

<div align="center">

[![Status](https://img.shields.io/badge/status-active-success.svg)]()
[![GitHub Issues](https://img.shields.io/github/issues/dahunni/spoti-sort.svg)](https://github.com/dahunnispoti-sort/issues)
[![GitHub Pull Requests](https://img.shields.io/github/issues-pr/dahunni/spoti-sort.svg)](https://github.com/dahunnispoti-sort/pulls)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](/LICENSE)

</div>

---

<p align="center"> An automated Docker / Python program to sort spotify playlists by "date-added" without re adding the songs or sorting them in the client. <br> This program was developed to sort a playlist for use in a Tesla, as its clien't doesn't allow any sorting
    <br> 
</p>

## 📝 Table of Contents

- [About](#about)
- [Getting Started](#getting_started)
- [Built Using](#built_using)
- [Contributing](../CONTRIBUTING.md)
- [Authors](#authors)
- [Acknowledgments](#acknowledgement)

## 🧐 About <a name = "about"></a>

You like to enjoy a spotify playlist ordered by the date a track was added (like any sane person should)? This docker enables that without relying on any client. This is perfect for use in a Tesla or any other client that doesn't support sorting.

## 🏁 Getting Started <a name = "getting_started"></a>

These instructions will get you a copy of the project up and running on your Docker environment. 


### Installing

The following steps will get your Conainer up and running

## Obtain spotify API keys

Head over to <a href="https://developer.spotify.com/dashboard">https://developer.spotify.com/dashboard</a> and log in.
Create a new application and note the Client ID and Client Secret. After that go into the settings and make sure to set the redirec URL to <code>https://localhost/</code>

## Create the Docker Container

<i>[Not published yet]For Unraid users use the Community Applications App </i>

You need to mount the following directorys of the container:
```
/config
```
<i>Your API Session will be stored in here</i>
<br></br>

You need to set the following variables:
```
CLIENT_ID
```
<i>Obtained from the Spotify API</i>

<br></br>
```
CLIENT_SECRET
```
<i>Obtained from the Spotify API</i>

<br></br>
```
PLAYLIST_IDS
```
<i>Comma separated playlist ids (with space!): xxxxxxxxxxxx, xxxxxxxxxxx</i>
<br></br>



## ⛏️ Built Using <a name = "built_using"></a>

- [Python](https://python.org/) - Programming language
- [Spotipy](https://github.com/plamere/spotipy/) - Spotify API integration
- [Docker](https://docker.com/) - Docker

## ✍️ Authors <a name = "authors"></a>

- [@dahunni](https://github.com/dahunnio) - Idea & Initial work

See also the list of [contributors](https://github.com/dahunni/spoti-sort/contributors) who participated in this project.

## 🎉 Acknowledgements <a name = "acknowledgement"></a>

- 
