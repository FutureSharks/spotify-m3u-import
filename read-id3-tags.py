#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import os
import sys
import pprint
import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from difflib import SequenceMatcher
from termcolor import colored
from mutagen import File

def parse_arguments():
    p = argparse.ArgumentParser(description='A script to import a m3u playlist into Spotify')
    p.add_argument('-f', '--file', help='Path to m3u playlist file', type=argparse.FileType('r'), required=True)
    p.add_argument('-u', '--username', help='Spotify username', required=True)
    p.add_argument('-d', '--debug', help='Debug mode', action='store_true', default=False)
    return p.parse_args()

def load_playlist_file(playlist_file):
    tracks = []
    try:
        content = [line.strip() for line in playlist_file if line.strip() and not line.startswith("#")]
    except Exception as e:
        logger.critical('Playlist file "%s" failed load: %s' % (playlist_file.name, str(e)))
        sys.exit(1)
    else:
        for track in content:
            if not os.path.exists(track):
                playlist_folder = os.path.dirname(playlist_file.name)
                track_fullpath = os.path.join(playlist_folder, track)
                if os.path.exists(track_fullpath):
                    track = track_fullpath
                else:
                    playlist_folder = os.getcwd()
                    track_fullpath = os.path.join(playlist_folder, track)
                    if os.path.exists(track_fullpath):
                        track = track_fullpath

            tracks.append({'path': track})
        return tracks

def read_tags(file_name):
    tag_data = False
    try:
        audio = File(file_name, easy=True)
        if audio:
            artist = audio.get('artist', [None])[0]
            title = audio.get('title', [None])[0]
            if artist and title:
                tag_data = {'artist': artist, 'title': title}
                logger.debug('Tags read from "%s": %s - %s' % (file_name, artist, title))
            else:
                logger.debug('No artist/title tags in "%s"' % file_name)
        else:
            logger.debug('Unsupported file format for "%s"' % file_name)
    except Exception as e:
        logger.debug('Failed to read tags from "%s": %s' % (file_name, str(e)))
    return tag_data

def guess_missing_track_info(file_name):
    guess = False
    filename = os.path.basename(file_name)
    filename_no_ext = os.path.splitext(filename)[0]
    for separator in ['-', '_']:
        track_parts = filename_no_ext.split(separator)
        if len(track_parts) > 1:
            guess = {'artist': track_parts[0].strip(), 'title': separator.join(track_parts[1:]).strip()}
            break
    return guess

def find_spotify_track(track):
    def _select_result_from_spotify_search(search_string, track_name, spotify_match_threshold):
        logger.debug('Searching Spotify for "%s" trying to find track called "%s"' % (search_string, track_name))
        def _how_similar(a, b):
            return SequenceMatcher(None, a, b).ratio()
        results_raw = sp.search(q=search_string, limit=30)
        if len(results_raw['tracks']['items']) > 0:
            spotify_results = results_raw['tracks']['items']
            logger.debug('Spotify results: %s' % len(spotify_results))
            for spotify_result in spotify_results:
                spotify_result['rank'] = _how_similar(track_name, spotify_result['name'])
                if spotify_result['rank'] == 1.0:
                    return {'id': spotify_result['id'], 'title': spotify_result['name'], 'artist': spotify_result['artists'][0]['name']}
            spotify_results_sorted = sorted(spotify_results, key=lambda k: k['rank'], reverse=True)
            if len(spotify_results_sorted) > 0 and spotify_results_sorted[0]['rank'] > spotify_match_threshold:
                return {'id': spotify_results_sorted[0]['id'], 'title': spotify_results_sorted[0]['name'], 'artist': spotify_results_sorted[0]['artists'][0]['name']}
        logger.debug('No good Spotify result found')
        return False
    spotify_match_threshold = 0.5
    # search by tags
    if track.get('tag_data') and 'artist' in track['tag_data'] and 'title' in track['tag_data']:
        spotify_search_string = '%s %s' % (track['tag_data']['artist'], track['tag_data']['title'])
        search_result = _select_result_from_spotify_search(
            spotify_search_string,
            track['tag_data']['title'],
            spotify_match_threshold
        )
        if search_result:
            return search_result
    # search by track['guess']
    if 'guess' in track and track['guess'] and 'artist' in track['guess'] and 'title' in track['guess']:
        spotify_search_string = '%s %s' % (track['guess']['artist'], track['guess']['title'])
        search_result = _select_result_from_spotify_search(
            spotify_search_string,
            track['guess']['title'],
            spotify_match_threshold
        )
        if search_result:
            return search_result
    return False

def format_track_info(track):
    if track.get('tag_data'):
        formatted_tag_data = '%s - %s' % (repr(track['tag_data']['artist']), repr(track['tag_data']['title']))
        formatted_guess = 'Not required'
    else:
        formatted_tag_data = colored('None', 'red')
        if track.get('guess'):
            formatted_guess = '%s - %s' % (repr(track['guess']['artist']), repr(track['guess']['title']))
        else:
            formatted_guess = colored('None', 'red')
    if track.get('spotify_data'):
        formatted_spotify = colored('%s - %s, %s' % (repr(track['spotify_data']['artist']), repr(track['spotify_data']['title']), repr(track['spotify_data']['id'])), 'green')
    else:
        formatted_spotify = colored('None', 'red')
    return '\n%s\nTag data: %s\nGuess from filename: %s\nSpotify: %s' % (
        colored(repr(track['path']), 'blue'),
        formatted_tag_data,
        formatted_guess,
        formatted_spotify
    )

if __name__ == "__main__":
    args = parse_arguments()
    spotify_username = args.username

    SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
    SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
    SPOTIPY_REDIRECT_URI = os.environ.get('SPOTIPY_REDIRECT_URI', 'http://127.0.0.1:8888/callback')  

    if not SPOTIPY_CLIENT_ID or not SPOTIPY_CLIENT_SECRET:
        print("Environment variables SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET must be set.")
        print("You can create them on https://developer.spotify.com/dashboard/")
        print("On Windows: setx SPOTIPY_CLIENT_ID \"your-client-id\"")
        print("On Linux/Mac: export SPOTIPY_CLIENT_ID=\"your-client-id\"")
        sys.exit(1)

    SCOPE = (
        "user-read-private "
        "user-read-email "
        "user-read-playback-state "
        "user-modify-playback-state "
        "user-read-currently-playing "
        "user-read-recently-played "
        "user-top-read "
        "user-library-read "
        "user-library-modify "
        "playlist-read-private "
        "playlist-read-collaborative "
        "playlist-modify-public "
        "playlist-modify-private "
        "user-follow-read "
        "user-follow-modify "
        "app-remote-control "
        "streaming "
        "user-read-playback-position"
    )
    # authenticate, first time a browser window will open and a .cache-[username] file will be created
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        username=spotify_username,
        scope=SCOPE
    ))

    user = sp.current_user()
    print(f"Logged-in As: {user['display_name']} ({user['id']})")

    logger = logging.getLogger(__name__)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        stdout_level = logging.DEBUG
    else:
        logger.setLevel(logging.CRITICAL)
        stdout_level = logging.CRITICAL

    tracks = load_playlist_file(args.file)

    print(colored('Parsed %s tracks from %s' % (len(tracks), args.file.name), 'green'))

    for track in tracks:
        track['tag_data'] = read_tags(track['path'])
        if not track['tag_data']:
            track['guess'] = guess_missing_track_info(track['path'])
        track['spotify_data'] = find_spotify_track(track)

        print(format_track_info(track))

    spotify_tracks = [k['spotify_data']['id'] for k in tracks if k.get('spotify_data')]
    spotify_playlist_name = os.path.basename(args.file.name)  # Using basename for playlist name

    if len(spotify_tracks) < 1:
        print('\nNo tracks matched on Spotify')
        sys.exit(0)

    print('\n%s/%s of tracks matched on Spotify, creating playlist "%s" on Spotify...' % (len(spotify_tracks), len(tracks), spotify_playlist_name))

    try:
        sp.trace = False

        playlist = sp.user_playlist_create(spotify_username, spotify_playlist_name, public=False)
        if len(spotify_tracks) > 100:
            def chunker(seq, size):
                return (seq[pos:pos + size] for pos in range(0, len(seq), size))
            for spotify_tracks_chunk in chunker(spotify_tracks, 100):
                results = sp.user_playlist_add_tracks(spotify_username, playlist['id'], spotify_tracks_chunk)
        else:
            results = sp.user_playlist_add_tracks(spotify_username, playlist['id'], spotify_tracks)
    except Exception as e:
        logger.critical('Spotify error: %s' % str(e))
    else:
        print('done\n')