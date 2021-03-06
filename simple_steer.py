import os
import math
import numpy as np
import h5py
import glob
from tqdm import tqdm
import scipy

import keras
from keras.models import Sequential, Model
from keras.layers.core import Dense, Dropout, Activation, Flatten, Reshape
from keras.layers import Embedding, Input, merge, ELU
from keras.layers.recurrent import SimpleRNN, LSTM
from keras.layers.convolutional import Convolution2D, MaxPooling2D
from keras.optimizers import SGD, Adam, RMSprop
from keras.regularizers import l2, activity_l2, l1
from keras.utils.np_utils import to_categorical
from keras import backend as K
import sklearn.metrics as metrics

from PIL import Image, ImageDraw

import matplotlib.pyplot as plt
plt.ion()


ndata = 0
imgsize = 64
# frame size
nrows = 64
ncols = 64
wr = 0.00001
dp = 0.

# speed, accel, distance, angle
real_in = Input(shape=(2,), name='real_input')

# video frame in, grayscale
frame_in = Input(shape=(3,nrows,ncols), name='img_input')

# convolution for image input
conv1 = Convolution2D(6,3,3,border_mode='same', W_regularizer=l1(wr), init='lecun_uniform')
conv_l1 = conv1(frame_in)
Econv_l1 = ELU()(conv_l1)
pool_l1 = MaxPooling2D(pool_size=(2,2))(Econv_l1)

conv2 = Convolution2D(8,3,3,border_mode='same', W_regularizer=l1(wr), init='lecun_uniform')
conv_l2 = conv2(pool_l1)
Econv_l2 = ELU()(conv_l2)
pool_l2 = MaxPooling2D(pool_size=(2,2))(Econv_l2)
drop_l2 = Dropout(dp)(pool_l2)

conv3 = Convolution2D(16,3,3,border_mode='same', W_regularizer=l1(wr), init='lecun_uniform')
conv_l3 = conv3(drop_l2)
Econv_l3 = ELU()(conv_l3)
pool_l3 = MaxPooling2D(pool_size=(2,2))(Econv_l3)

drop_l3 = Dropout(dp)(pool_l3)

flat = Flatten()(drop_l3)

M = merge([flat,real_in], mode='concat', concat_axis=1)

D1 = Dense(32,W_regularizer=l1(wr), init='lecun_uniform')(M)
ED1 = ELU()(D1)
DED1 = Dropout(dp)(ED1)

S1 = Dense(64,W_regularizer=l1(wr), init='lecun_uniform')(DED1)
ES1 = ELU()(S1)

# Custom activation to clamp values to 0-1
from keras import initializations
from keras.engine import Layer
import keras.backend as K

class ClampedLinear(Layer):
    '''Thresholded Linear Activation:
    `f(x) = x for x > alpha and x < beta`
    `f(x) = alpha for x <= alpha`.
    `f(x) = beta for x >= beta`.
    # Input shape
        Arbitrary. Use the keyword argument `input_shape`
        (tuple of integers, does not include the samples axis)
        when using this layer as the first layer in a model.
    # Output shape
        Same shape as the input.
    # Arguments
        alpha: float >= 0. Left threshold
        beta:  float >= 0. Right threshold
    '''
    def __init__(self, alpha=0., beta = 1.0, **kwargs):
        self.supports_masking = True
        self.alpha = K.cast_to_floatx(alpha)
        self.beta = K.cast_to_floatx(beta)
        super(ClampedLinear, self).__init__(**kwargs)
    def call(self, x, mask=None):
        y = K.maximum(x,self.alpha)
        z = K.minimum(y,self.beta)
        return z
    def get_config(self):
        config = {'alpha': float(self.alpha),
                  'beta': float(self.beta),}
        base_config = super(ClampedLinear, self).get_config()
        return dict(list(base_config.items()) + list(config.items())) 

clamp = ClampedLinear()

#Steer_node = Dense(1, name='steer_node', init='lecun_uniform')(ES1)
#Steer_out = Activation(clamp,name='steer_out')(Steer_node)
Steer_out = Dense(1, activation='linear', name='steer_out', init='lecun_uniform')(ES1)

model = Model(input=[real_in, frame_in], output=[Steer_out])

sgd = SGD(lr=0.003)
adam = Adam(lr=0.001)


model.compile(loss=['mse'],
              optimizer=adam,
              metrics=['mse'])

# Switch to the compressed data input
# Huzzah!

imgs = np.load('data/imgs_arr_big.npz')['arr_0']
speedx = np.load('data/speedx_arr_big.npz')['arr_0']
targets = np.load('data/targets_arr_big.npz')['arr_0']
nb_epoch = 100
mini_epoch = 10
num_steps = int(nb_epoch/mini_epoch)
for step in tqdm(range(5,num_steps)):
    h = model.fit([speedx, imgs], {'steer_out':targets[:,0]},
                    batch_size = 32, nb_epoch=mini_epoch, verbose=1,
                    validation_split=0.1, shuffle=True)
    model.save_weights('steer_nodrop_l2_big_{0}_{1:4.5}.h5'.format(step,h.history['val_loss'][-1]),overwrite=True)

model.save_weights('steer_only_l2_big_final.h5',overwrite=True)
model.load_weights('steer_only_l2_big_final.h5')

W = model.get_weights()

# look at conv filters separately in color channel
f, con = plt.subplots(4,3, sharex='col', sharey='row')
for row in range(4):
    for col in range(3):
        con[row,col].pcolormesh(W[0][row,col],cmap=plt.cm.hot)


# combine color channels into on filter image
f, con = plt.subplots(4,1, sharex='col', sharey='row')
for row in range(4):
    con[row].imshow(W[0][row].transpose((1,2,0)),
                    interpolation="none")

preds = model.predict([speedx,imgs])
steer_preds = preds.reshape([-1])

#FYI 1.0 is to the left, 0. is to the right
# unless I'm mixing up my directions in the animation


# plot predictions and actual
plt.plot(np.array([steer_preds.reshape(len(steer_preds)),targets[:,0]]).T,'.')
plt.plot(steer_preds.reshape(len(steer_preds)),targets[:,0],'.')

# Animation!
def get_point(s,start=0,end=63,height= 16):
    X = int(s*(end-start))
    if X < start:
        X = start
    if X > end:
        X = end
    return (X,height)


# evaluate the model at each point
mse = []
all_preds = []
weights = sorted(glob.glob('steer_nodrop*.h5'),
        key = lambda x: int(x.split('_')[4]) )
val_idx = (len(imgs)//10) * 9
for wfile in tqdm(weights):
    model.load_weights(wfile)
    preds = model.predict([speedx[val_idx:],imgs[val_idx:]])
    steer_preds = preds.reshape([-1])
    all_preds.append(steer_preds)
    mse.append(metrics.mean_squared_error(targets[val_idx:,0],steer_preds))


plt.plot(mse)

import matplotlib.animation as animation
figure = plt.figure()
imageplot = plt.imshow(np.zeros((64, 64, 3), dtype=np.uint8))
def next_frame(i):
    im = Image.fromarray(np.array(imgs[val_idx+i].transpose(1,2,0),dtype=np.uint8))
    p = get_point(1-steer_preds[i])
    t = get_point(1-targets[i+val_idx,0])
    draw = ImageDraw.Draw(im) 
    draw.line((32,63, p,p),
                fill=(255,0,0,128))
    draw.line((32,63, t,t),
                fill=(0,255,0,128))
    imageplot.set_array(im)
    #if i % 10 == 0:
    #    print(i)
    return imageplot,
animate = animation.FuncAnimation(figure, next_frame, frames=range(0,len(imgs)), interval=25, blit=False)
plt.show()

# Muck with default symbol cycler
from itertools import cycle, product
from cycler import cycler
C1 = plt.rcParams['axes.prop_cycle']
C = [c['color'] for c in list(C1)]
sym = [".","x","+","v"]
S1 = cycler('marker',sym)
#P = ['{0}{1}'.format(c,s) for c,s in product(C,sym)]
P = S1*C1
plt.rc('axes', prop_cycle=P)
lines = plt.plot(np.array( [targets[val_idx:,0]] + [sp.reshape(len(steer_preds)) for sp in all_preds]).T, linestyle='')
plt.legend(lines, ['target'] + [str(i) for i in range(len(all_preds))])
