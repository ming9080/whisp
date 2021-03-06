import os
import time
import datetime
from PIL import Image
from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from fastai.vision import *
import torch
from pathlib import Path
from io import BytesIO
import sys
import uvicorn
import aiohttp
import asyncio
import pylab
from PIL import Image
import matplotlib
matplotlib.use('agg')

from matplotlib import pyplot as plt
from matplotlib import cm
from tqdm import tqdm
import pylab

import librosa
from librosa import display
import numpy as np

import boto3, botocore

from os.path import join, dirname
from dotenv import load_dotenv

import pymongo
from pymongo import MongoClient

#load env variables from .env file
dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)

s3 = boto3.client(
   "s3",
   aws_access_key_id=os.environ.get('WHISP_S3_KEY'),
   aws_secret_access_key=os.environ.get('WHISP_S3_SECRET_ACCESS_KEY')
)

mongo_client = MongoClient(os.environ.get('WHISP_MONGO_DB'))
db = mongo_client.whisp
sounds = db.sounds


templates = Jinja2Templates(directory='app/templates')

app = Starlette(debug=True)

app.mount('/static', StaticFiles(directory='app/static'))

path = Path(__file__).parent

classes = ['airplane', 'breathing', 'brushing_teeth', 'can_opening', 'car_horn', 'cat', 'chainsaw', 'chirping_birds', 'church_bells', 'clapping', 'clock_alarm', 'clock_tick', 'coughing', 'cow', 'crackling_fire', 'crickets', 'crow', 'crying_baby', 'dog', 'door_wood_creaks', 'door_wood_knock', 'drinking_sipping', 'engine', 'fireworks', 'footsteps', 'frog', 'glass_breaking', 'hand_saw', 'helicopter', 'hen', 'insects', 'keyboard_typing', 'laughing', 'mouse_click', 'pig', 'pouring_water', 'rain', 'rooster', 'sea_waves', 'sheep', 'siren', 'sneezing', 'snoring', 'thunderstorm', 'toilet_flush', 'train', 'vacuum_cleaner', 'washing_machine', 'water_drops', 'wind']

learn = load_learner(path, 'models/export.pkl')

@app.route('/')
async def homepage(request):
    return templates.TemplateResponse('index.html', {'request': request})

@app.route("/upload", methods=["POST"])
async def upload(request):
    form = await request.form()
    bytes = await (form["file"].read())
    wav = BytesIO(bytes) #not my favorite way to do this but it works ;)

    utc_time = str(int(time.time()))
    sound_file = "tmp/sound_" + utc_time + ".wav"
    image_file = "tmp/image_" + utc_time + ".jpg"
    
    with open(sound_file, 'wb') as f:
        f.write(wav.getvalue())
    wav.close()

    #convert sound to image
    y, sr = librosa.load(sound_file, sr = 22050) # Use the default sampling rate of 22,050 Hz
    #y, sr = soundfile.read(source_filepath) try this again later

    # Pre-emphasis filter
    pre_emphasis = 0.97
    y = np.append(y[0], y[1:] - pre_emphasis * y[:-1])

    # Compute spectrogram
    M = librosa.feature.melspectrogram(y, 
                                       sr, 
                                       fmax = sr/2, # Maximum frequency to be used on the on the MEL scale        
                                       n_fft=2048, 
                                       hop_length=512, 
                                       n_mels = 96, # As per the Google Large-scale audio CNN paper
                                       power = 2) # Power = 2 refers to squared amplitude
    log_power = librosa.power_to_db(M, ref=np.max)# Covert to dB (log) scale
    
    # Plotting the spectrogram and save as JPG without axes (just the image)
    pylab.axis('off') 
    pylab.axes([0., 0., 1., 1.], frameon=False, xticks=[], yticks=[]) # Remove the white edge
    librosa.display.specshow(log_power, cmap=cm.jet)
    pylab.savefig(image_file, bbox_inches=None, pad_inches=0)
    pylab.close()

    img_bytes = BytesIO()
    with open(image_file, 'rb') as f:
         img_bytes = BytesIO(f.read())

    img = open_image(img_bytes)
    _,_,losses = learn.predict(img)

    #delete temp files
    if os.path.exists(sound_file):
        os.remove(sound_file)
    if os.path.exists(image_file):
        os.remove(image_file)

    return JSONResponse({
        "predictions": sorted(
            zip(classes, map(float, losses)),
            key=lambda p: p[1],
            reverse=True
        )
    })

@app.route("/upload-category", methods=["POST"])
async def upload(request):
    form = await request.form()
    bytes = await (form["file"].read())
    wav = BytesIO(bytes) #not my favorite way to do this but it works ;)

    utc_time = str(int(time.time()))
    sound_filename = "tmp/sound_" + utc_time + ".wav"
    
    with open(sound_filename, 'wb') as f:
        f.write(wav.getvalue())
    wav.close()

    guessed_category = form["guessed_category"]
    print(guessed_category)

    select_category = form["select_category"]
    print(select_category)

    select_category_fill_in = form["select_category_fill_in"]
    print(select_category_fill_in)

    s3_file_path = ""
    # post file
    with open(sound_filename, "rb") as f:
        try:
            s3.upload_fileobj(
                f, 
                os.environ.get('WHISP_S3_BUCKET'), 
                sound_filename[4:], #remove tmp/ 
                ExtraArgs={
                    "ACL": "public-read"
                }
             )
        except Exception as e:
            print("Something Happened: ", e)
            return e
        s3_file_path = "{}{}".format('http://{}.s3.amazonaws.com/'.format(os.environ.get('WHISP_S3_BUCKET')), sound_filename[4:])
        print(s3_file_path)


    sound_data = {"url": s3_file_path,
                 "guessed_category": guessed_category,
                 "select_category": select_category,
                 "select_category_fill_in": select_category_fill_in,
                 "date": datetime.datetime.utcnow()}

    sounds.insert_one(sound_data)

    #delete temp files
    if os.path.exists(sound_filename):
        os.remove(sound_filename)

    return JSONResponse({"response": "ok"})

if __name__ == "__main__":
    if "serve" in sys.argv:
        uvicorn.run(app, host="0.0.0.0", port=8008)
