from django import forms
from .models import DocModel

class DocumentForm(forms.ModelForm):
    class Meta:
        model = DocModel
        fields = ('vid', )

class StreamURLForm(forms.Form):
    stream_url = forms.URLField(
        label='Stream URL', 
        required=False, 
        help_text='Enter the URL of the video stream.',
        widget=forms.URLInput(attrs={'placeholder': 'http://', 'class': 'form-control'})
    )
