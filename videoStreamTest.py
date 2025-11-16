import cv2

def play_mp4(file_path):
    """
    Plays an MP4 video file.

    Args:
        file_path (str): The path to the MP4 file.
    """
    cap = cv2.VideoCapture(file_path)

    if not cap.isOpened():
        print(f"Error: Could not open video file '{file_path}'.")
        return

    while True:
        ret, frame = cap.read()

        if not ret:
            print("End of video stream or error reading frame.")
            break

        cv2.imshow('Video Player', frame)

        # Press 'q' to quit the video playback
        if cv2.waitKey(25) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

# Example usage:
video_file = '/Users/ayushpai/Documents/Movie on 11-15-25 at 3.43 PM.mp4'  # Replace with the actual path to your MP4 file
play_mp4(video_file)




