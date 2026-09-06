# IPTV: Movies, Animation, Kids — UA / RU / EN

Add this playlist URL to IPTVX / VIXO:

https://raw.githubusercontent.com/vibe-master-ai/my-iptv/main/my-iptv.m3u

The playlist is regenerated daily at 04:23 UTC by GitHub Actions, independently of any local computer. Scheduled runs may be delayed by GitHub. A successful run updates status.json even when stream URLs are unchanged.

Sources: https://github.com/iptv-org/iptv and https://github.com/iptv-org/api.

Filtering retains Movies, Animation and Kids streams matched to the Ukrainian, Russian or English language playlists, or channels whose country is UA/RU in the channel database. Country fallback intentionally allows channels with missing language metadata. Identical stream URLs are deduplicated across categories; alternative URLs for a channel are retained.

Downloads are retried; invalid or empty sources and unexpectedly large drops fail the update, preserving the published playlist. Status and manual update: repository Actions → Update IPTV playlist → Run workflow.

Playlist generation does not verify playback. Some streams may be offline or geographically restricted. This repository contains public stream links, not video hosting.

Local use: Python 3, run `python3 iptv_filter.py`.
