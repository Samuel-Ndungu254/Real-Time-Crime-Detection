from django.views.decorators.clickjacking import xframe_options_exempt
import numpy as np
from django.shortcuts import render, redirect, reverse
from django.http import JsonResponse, HttpResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from .models import DocModel
import json
from django.http import StreamingHttpResponse, HttpResponseServerError
from .models import DocModel
from django.views.decorators import gzip
from django.core.mail import send_mail
import os
import django
import time
import cv2
from urllib.parse import urlparse
from .forms import DocumentForm
from .forms import StreamURLForm
from django.conf import settings
from django.shortcuts import get_object_or_404
import logging
logger = logging.getLogger(__name__)
model = settings.MODEL

# Set up the Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Website.settings')
django.setup()

  

class VideoCamera(object):
    def __init__(self, source=None):
        self.font = cv2.FONT_HERSHEY_SIMPLEX
        self.status = True
        self.org = (50, 80)
        self.fontScale = 1.4
        self.thickness = 3
        self.SIZE = (150, 150)
        self.THRESH = 0.76
        self.source = source
        self.video = self.initialize_video_capture(self.source)
        self.skipCount = 2
        self.prev = None
        self.fcount = 0
        self.last_email_time = 0
        self.email_cooldown = 500  # Cooldown in seconds

    def initialize_video_capture(self, source):
        """Determine if source is a URL or file path and initialize VideoCapture."""
        if source is None:
            # Default to the first webcam if no source provided
            return cv2.VideoCapture(0)
        elif self.is_url(source):
            # Source is a URL
            return cv2.VideoCapture(source)
        else:
            # Assume source is a file path; check if it needs to be prefixed with './'
            if not os.path.exists(source):
                source = './' + source
            return cv2.VideoCapture(source)
    
    def is_url(self, source):
        """Check if the source string is a URL."""
        try:
            result = urlparse(source)
            return all([result.scheme, result.netloc])
        except ValueError:
            return False
        
    def __del__(self):
        self.video.release()

    def get_frame(self):
        ret, image = self.video.read()
        if not ret:
            self.video.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, image = self.video.read()
            # For live streams, consider logging an error or retrying with a backoff strategy
            self.status = False

        if ret and image is not None:
            if self.fcount % self.skipCount == 0:
                processed_frame = self.process_frame(image)
                return processed_frame
            else:
                string = self.prev if self.prev is not None else "Error: Previous frame not available"
                return self.render_frame(image, string)
        else:
            self.status = False
            return None

    def process_frame(self, image):
        tmp = cv2.resize(image, self.SIZE)
        tmp = tmp / 255.0
        pred = model.predict(np.array([tmp]))

        logger.info(f"Model prediction: {pred[0][0]}")
        string = "Suspicious" if pred[0][0] > self.THRESH else "Peaceful"
        self.prev = string

        if string == "Suspicious":
            current_time = time.time()
            if current_time - self.last_email_time > self.email_cooldown:
                self.send_notification(pred[0][0])
                self.last_email_time = current_time

        return self.render_frame(image, string)

    def render_frame(self, image, text):
        color = (255, 255, 255)
        filled_color = (0, 200, 100) if text.split(' ')[0] == 'Peaceful' else (0, 0, 255)
        image = cv2.rectangle(image, (20, 20), (600, 100), filled_color, cv2.FILLED)
        image = cv2.putText(image, text, self.org, self.font, self.fontScale, color, self.thickness, cv2.LINE_AA)
        ret, jpeg = cv2.imencode('.jpg', image)
        self.fcount += 1
        return jpeg.tobytes()

    def send_notification(self, probability):
        logger.info(f"Attempting to send email notification, model prediction: {probability}")
        subject = 'Suspicious Activity Detected!'
        message = f'Suspicious activity was detected with a probability of {probability}. Please check the surveillance feed for more details.'
        email_from = settings.EMAIL_HOST_USER
        recipient_list = ['samuelndiritu265@gmail.com']  
        send_mail(subject, message, email_from, recipient_list, fail_silently=False)
        

def gen(camera):
    while True:
        frame = camera.get_frame()
        if frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n')
        else:
            break
def Stream(request):
    # Assuming the URL is stored in the session after being submitted by the user
    stream_url = request.session.get('stream_url', None)

    if not stream_url:
        # Fallback if no URL is provided in the session
        # Attempt to get the last entry from the database as a secondary option
        entry = DocModel.objects.all().last()
        if entry:
            stream_url = entry.vid.url
        else:
            # Handle case where no stream URL is available
            return HttpResponseServerError("No video stream URL provided.")

    try:
        # Initialize your VideoCamera with the URL
        return StreamingHttpResponse(gen(VideoCamera(stream_url)),
                                     content_type="multipart/x-mixed-replace;boundary=frame")
    except HttpResponseServerError as e:
        # Consider using logging here
        print("Stream aborted due to an error:", e)
        return HttpResponseServerError("Error encountered while attempting to stream video.")


@gzip.gzip_page
def StreamToken(request, token):
    try:
        entry = DocModel.objects.filter(stoken=token).last()
        return StreamingHttpResponse(gen(VideoCamera(entry.vid.url)), content_type="multipart/x-mixed-replace;boundary=frame")
    except StreamingHttpResponse.HttpResponseServerError as e:
        print("aborted")


def HomeView(request):
    context = {
        'doc_form': DocumentForm(),  # Initial empty form
        'stream_url_form': StreamURLForm()  # Initial empty form for live stream URLs
    }

    if request.method == 'POST':
        doc_form = DocumentForm(request.POST, request.FILES)
        if doc_form.is_valid():
            # Save the form and get the instance
            instance = doc_form.save()
            # Clear any existing live stream URL
            request.session.pop('stream_url', None)
            # Set the video URL in the session for uploaded video
            request.session['uploaded_video_url'] = instance.vid.url
            return redirect('stream')
        else:
            context['doc_form'] = doc_form

    return render(request, 'home.html', context)





# A view to handle stream URL submission and initiate streaming
def StartStreamView(request):
    if request.method == 'POST':
        stream_url_form = StreamURLForm(request.POST)
        if stream_url_form.is_valid():
            stream_url = stream_url_form.cleaned_data['stream_url']
            request.session['stream_url'] = stream_url
            return redirect('stream')  # Use the correct URL pattern name
    else:
        stream_url_form = StreamURLForm()
    return render(request, 'home.html', {'stream_url_form': stream_url_form})





@xframe_options_exempt
def StreamView(request):
    context = {
        'message': 'No Video Files Yet!',
        'is_streaming': False,
    }

    # Check for a live stream URL in the session
    stream_url = request.session.get('stream_url', None)
    uploaded_video_url = request.session.get('uploaded_video_url', None)

    # Determine which type of video to display
    if stream_url:
        context['is_streaming'] = True
        context['stream_url'] = stream_url
        # Use the live_stream.html template for live streams
        return render(request, 'live_stream.html', context)
    elif uploaded_video_url:
        context['is_streaming'] = True
        context['video_url'] = uploaded_video_url
        # Use the stream.html template for uploaded videos
        return render(request, 'stream.html', context)
    else:
        # If there is no URL available, render stream.html with a message
        return render(request, 'stream.html', context)







# API End Point
def StreamTokenView(request, token):
    # Fetch the video entry associated with the given token or return a 404 error if not found
    entry = get_object_or_404(DocModel, stoken=token)
    
    # Assuming you store a URL or file path in entry.vid.url, initialize the camera
    camera = VideoCamera(entry.vid.url)

    # Stream the video
    try:
        return StreamingHttpResponse(gen(camera), content_type="multipart/x-mixed-replace;boundary=frame")
    except Exception as e:
        logger.error(f"Error streaming video for token {token}: {e}")
        return JsonResponse({'message': f'Error streaming video for token {token}: {e}'})


@csrf_exempt
def APIEnd(request):
    if request.method == 'POST':
        try:
            stoken = request.POST.get('stoken')
            vidFile = request.FILES.get('vid')
            stream_url = request.POST.get('stream_url')

            if vidFile:
                # Handle video file upload
                DocModel(stoken=stoken, vid=vidFile).save()
                baseurl = request.build_absolute_uri(reverse('home'))
                response_data = {'status': 'ok', 'message': 'Video file received.', 'vidurl': baseurl+'streamtoken/'+stoken}

            elif stream_url:
                # Handle stream URL
                # Save the stream URL to the database, associated with the token
                # You need to modify your DocModel to store the stream URL or handle it appropriately
                response_data = {'status': 'ok', 'message': 'Stream URL received.', 'streamurl': stream_url}
            
            else:
                return JsonResponse({'status': 'error', 'message': 'No video file or stream URL provided.'}, status=400)
            
            return JsonResponse(response_data)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    return JsonResponse({'status': 'error', 'message': 'Only POST requests are accepted.'}, status=405)

# Create your views here.