# Note:

1. Create a new directory called videoFiles
2. Create a video on your computer and format it as an .mp4 (perhaps 30 seconds)
3. Trim the video into even segments (3 segments of 10 seconds each)
4. Put each respective .mp4 into the videoFiles
5. python run_network.py



# Other stuff:


- right now, the start time for a peer is the time when first chunk is received (line 86 in peer.py)
- the end time (after the last chunk has been sent across to the next peer), is on line 165