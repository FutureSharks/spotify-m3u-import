#!/usr/bin/env python
# -*- coding: utf-8 -*-

import eyed3
import argparse
import os
import sys
import pprint
import logging
import logging.handlers
import spotipy
import spotipy.util as util
from difflib import SequenceMatcher
from termcolor import colored

def parse_arguments():
    p = argparse.ArgumentParser(description='A script to import a m3u playlist into Spotify')
    p.add_argument('-f', '--file', help='Path to m3u playlist file', type=argparse.FileType('r'), required=True)
    p.add_argument('-u', '--username', help='Spotify username', required=True)
    p.add_argument('-d', '--debug', help='Debug mode', action='store_true', default=False)
    return p.parse_args()

def load_playlist_file(playlist_file):
    tracks = []
    try:
        content = [ line.strip() for line in playlist_file if line.strip() and not line.startswith("#") ]
    except Exception as e:
        logger.critical('Playlist file "%s" failed load: %s' % (playlist_file, str(e)))
        sys.exit(1)
    else:
        for track in content:
            tracks.append({'path': track})
        return tracks

def read_id3_tags(file_name):
    tag_data = False
    try:
        track_id3 = eyed3.load(file_name)
    except Exception as e:
        logger.debug('Track "%s" failed ID3 tag load: %s' % (track, str(e)))
    else:
        logger.debug('Reading tags from "%s"' % track)
        if track_id3.tag is not None:
            if track_id3.tag.artist is not None and track_id3.tag.title is not None:
                tag_data = {'artist': track_id3.tag.artist, 'title': track_id3.tag.title}
    return tag_data

def guess_missing_track_info(file_name):
    guess = False
    filename = os.path.basename(file_name)
    filename_no_ext = os.path.splitext(filename)[0]
    track_uri_parts = filename_no_ext.split('-')
    if len(track_uri_parts) > 1:
        guess = {'filename': {} }
        guess['artist'] = track_uri_parts[0].strip()
        guess['title'] = track_uri_parts[1].strip()
    return guess

def find_spotify_track(track):
    def _select_result_from_spotify_search(search_string, track_name, spotify_match_threshold):
        logger.debug('Searching Spotify for "%s" trying to find track called "%s"' % (search_string, track_name))
        def _how_similar(a, b):
            return SequenceMatcher(None, a, b).ratio()
        results_raw = sp.search(q=search_string, limit=30)
        if len(results_raw['tracks']['items']) > 0:
            spotify_results = results_raw['tracks']['items']
            logger.debug('Spotify results:%s' % len(spotify_results))
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
    # search by id3 tags
    if track['id3_data'] and 'artist' in track['id3_data'] and 'title' in track['id3_data']:
        spotify_search_string = '%s %s' % (track['id3_data']['artist'], track['id3_data']['title'])
        seach_result = _select_result_from_spotify_search(
            spotify_search_string,
            track['id3_data']['title'],
            spotify_match_threshold
        )
        if seach_result:
            return seach_result
    # search by track['guess']
    if 'guess' in track and track['guess'] and 'artist' in track['guess'] and 'title' in track['guess']:
        spotify_search_string = '%s %s' % (track['guess']['artist'], track['guess']['title'])
        seach_result = _select_result_from_spotify_search(
            spotify_search_string,
            track['guess']['title'],
            spotify_match_threshold
        )
        if seach_result:
            return seach_result
    return False

def format_track_info(track):
    if track['id3_data']:
        formatted_id3_data = '%s - %s' % (repr(track['id3_data']['artist']), repr(track['id3_data']['title']))
        formatted_guess = 'Not required'
    else:
        formatted_id3_data = colored('None', 'red')
        if track['guess']:
            formatted_guess = '%s - %s' % (repr(track['guess']['artist']), repr(track['guess']['title']))
        else:
            formatted_guess = colored('None', 'red')
    if track['spotify_data']:
        formatted_spotify = colored('%s - %s, %s' % (repr(track['spotify_data']['artist']), repr(track['spotify_data']['title']), repr(track['spotify_data']['id'])), 'green')
    else:
        formatted_spotify = colored('None', 'red')
    return '\n%s\nIDv3 tag data: %s\nGuess from filename: %s\nSpotify: %s' % (
        colored(repr(track['path']), 'blue'),
        formatted_id3_data,
        formatted_guess,
        formatted_spotify
    )

if __name__ == "__main__":
    args = parse_arguments()
    sp = spotipy.Spotify()

    logger = logging.getLogger(__name__)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        stdout_level = logging.DEBUG
    else:
        logger.setLevel(logging.CRITICAL)
        eyed3.log.setLevel("ERROR")
        stdout_level = logging.CRITICAL

    tracks = load_playlist_file(args.file)

    print colored('Parsed %s tracks from %s' % (len(tracks), args.file.name), 'green')

    for track in tracks:
        track['id3_data'] = read_id3_tags(track['path'])
        if not track['id3_data']:
            track['guess'] = guess_missing_track_info(track['path'])
        track['spotify_data'] = find_spotify_track(track)

        print format_track_info(track)

    spotify_tracks = [ k['spotify_data']['id'] for k in tracks if k.get('spotify_data') ]
    spotify_playlist_name = args.file.name
    spotify_username = args.username

    if len(spotify_tracks) < 1:
        print '\nNo tracks matched on Spotify'
        sys.exit(0)

    print '\n%s/%s of tracks matched on Spotify, creating playlist "%s" on Spotify...' % (len(spotify_tracks), len(tracks), spotify_playlist_name),

    token = util.prompt_for_user_token(spotify_username, 'playlist-modify-private')

    if token:
        try:
            sp = spotipy.Spotify(auth=token)
            sp.trace = False
            playlist = sp.user_playlist_create(spotify_username, spotify_playlist_name, public=False)
            if len(spotify_tracks) > 100:
                def chunker(seq, size):
                    return (seq[pos:pos + size] for pos in xrange(0, len(seq), size))
                for spotify_tracks_chunk in chunker(spotify_tracks, 100):
                    results = sp.user_playlist_add_tracks(spotify_username, playlist['id'], spotify_tracks_chunk)
            else:
                results = sp.user_playlist_add_tracks(spotify_username, playlist['id'], spotify_tracks)
        except Exception as e:
            logger.critical('Spotify error: %s' % str(e))
        else:
            print 'done\n'
    else:
        logger.critical('Can\'t get token for %s user' % spotify_username)
