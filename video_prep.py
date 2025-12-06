from moviepy import VideoFileClip
import os

def split_video(input_path, output_dir, k):
    '''
    Splits a video in .mp4 format into k subvideos of even length for
    testing strandcast implementation
    
    :param input_path: The path of the original .mp4 file
    :param output_dir: Where the split videos are stored
    :param k: The video will be split into k subvideos of even length
    '''
    video = VideoFileClip(input_path)
    duration = video.duration # in seconds

    segment_length = duration / k # length of each subvideo

    os.makedirs(output_dir, exist_ok=True)  #makes sure the given output directory exists

    for i in range(k):
        start = i * segment_length
        end = (i +1) * segment_length
        if end > duration:
            end = duration

        output_path = os.path.join(output_dir, f"test{i+1}.mp4")
        print(f"test{i+1}.mp4 done")
        subclip = video.subclipped(start, end)
        subclip.write_videofile(output_path, codec="libx264", audio_codec='aac')
    video.close()
    print("You are now ready to test! Run 'python run_network.py' to test out our implementation")

######################################################
# Create your test videos here!

# Input video
input_video_path = r"C:\Users\Miguel Alvarado\Downloads\unc_video.mp4"

# Output videos destination
output_video_path = r"C:\Users\Miguel Alvarado\strandcast\videoFiles"  # must end in "strandcast\videoFiles" for correct testing functionality

# Number of subvideos (Three for this testing)
k = 3

split_video(input_video_path, output_video_path, k)