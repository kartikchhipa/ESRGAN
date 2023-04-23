
import sys
import os.path
import glob
import cv2
import numpy as np
import torch
import architecture as arch
import torchvision
import torch.nn as nn
import matplotlib.pyplot as plt
import torch.optim as optim
from tqdm.auto import tqdm
import math
import wandb
wandb.init(project="Forecasting Inflation No Pretrained Weights", config={"epochs":200})

# hyper parameters
config = wandb.config
epochs = config.epochs
lr = 1e-3

# import the pretrained ESRGAN
device = torch.device('cuda')  # if you want to run on CPU, change 'cuda' -> cpu

# folder for low res and corresponding hi res image
train_LR_folder = 'input/dataset/train/train_LR/*'
train_HR_folder = 'input/dataset/train/train_HR/'
test_LR_folder = 'input/dataset/test/test_LR/*'
test_HR_folder = 'input/dataset/test/test_HR/'

# initialize the feature extractor vgg19 model
loss_model = torchvision.models.vgg19(pretrained=False).cuda()
vgg19_54 = nn.Sequential(*list(loss_model.features.children())[:9])
vgg19_22 = nn.Sequential(*list(loss_model.features.children())[:3])


def PSNR(label, outputs, max_val=1):
    """
    Compute Peak Signal to Noise Ratio (the higher the better).
    PSNR = 20 * log10(MAXp) - 10 * log10(MSE).
    https://en.wikipedia.org/wiki/Peak_signal-to-noise_ratio#Definition
    First we need to convert torch tensors to NumPy operable.
    """
    label = label.cpu().detach().numpy()
    outputs = outputs.cpu().detach().numpy()
    img_diff = outputs - label
    rmse = math.sqrt(np.mean((img_diff) ** 2))
    if rmse == 0:
        return 100
    else:
        PSNR = 20 * math.log10(max_val / rmse)
        return PSNR
    

# setup skeleton architecture
model = arch.RRDB_Net(3, 3, 64, 23, gc=32, upscale=4, norm_type=None, act_type='leakyrelu', \
                        mode='CNA', res_scale=1, upsample_mode='upconv')

# switch model to training mode
model.train()
for k, v in model.named_parameters():
    v.requires_grad = True
model = model.to(device)

# initialize criterion and optimizer
criterion = nn.MSELoss().cuda()
optimizer = optim.SGD(model.parameters(), lr=lr, momentum=0)


# Training model from scratch
wandb.watch(model, criterion, log="all", log_freq=10)
print('\nTraining...')
idx = 0
running_loss = 0.0
for epoch in range(epochs):
    optimizer.zero_grad()
    running_loss = 0
    batch_psnr=0
    for path in tqdm(glob.glob(train_LR_folder)):
        idx += 1

        # zero the parameter gradients
        base = os.path.splitext(os.path.basename(path))[0]
        # read image
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        img = img * 1.0 / 255
        img = torch.from_numpy(np.transpose(img[:, :, [2, 1, 0]], (2, 0, 1))).float()
        img_LR = img.unsqueeze(0)
        img_LR = img_LR.to(device)
        output = model(img_LR)
        out_feats = vgg19_22(output)

        # extract features of target image
        target_file = train_HR_folder+base+".tiff"
        target_img = cv2.imread(target_file, cv2.IMREAD_COLOR)
        target_img = target_img * 1.0 / 255
        target_img = torch.from_numpy(np.transpose(target_img[:, :, [2, 1, 0]], (2, 0, 1))).float()
        target_img_LR = target_img.unsqueeze(0)
        target_img_LR = target_img_LR.to(device)
        target_feats = vgg19_22(target_img_LR)

        # compute loss between output and target
        # print('target feats',target_feats)
        # print('output feats',out_feats)
        loss = criterion(out_feats, target_feats)
        running_loss += loss.item()
        loss.backward()
        optimizer.step()

        batch_psnr += PSNR(target_img_LR, output)

    avg_psnr = batch_psnr / len(glob.glob(train_LR_folder))

    wandb.log({'epoch': epoch+1, 'loss': running_loss, 'psnr': avg_psnr})
    print("epoch: %d, loss: %f | PSNR: %fdb" %(epoch, running_loss, avg_psnr))



# switch model to testing mode
model.eval()
for k, v in model.named_parameters():
    v.requires_grad = False
model = model.to(device)
print('\nTesting...')
idx = 0
for path in glob.glob(test_LR_folder):
    idx += 1
    base = os.path.splitext(os.path.basename(path))[0]


    # read image
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    img = img * 1.0 / 255
    img = torch.from_numpy(np.transpose(img[:, :, [2, 1, 0]], (2, 0, 1))).float()
    img_LR = img.unsqueeze(0)
    img_LR = img_LR.to(device)

    # generate HR image
    output = model(img_LR).data.squeeze().float().cpu().clamp_(0, 1).numpy()

    # save generated image to output folder
    output_img = np.transpose(output[[2, 1, 0], :, :], (1, 2, 0))
    output_img = (output_img * 255.0).round()
    output_img = output_img.astype(np.uint8)
    plt.imsave('output/remote/{:s}_trained.tiff'.format(base), output_img, cmap='gray')

# save trained model
torch.save(model.state_dict(), "models/RRDB_ESRGAN_remote_train.pth")