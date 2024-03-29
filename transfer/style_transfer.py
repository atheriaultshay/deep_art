#style_transfer.py

from __future__ import print_function

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import numpy as np

from PIL import Image
import matplotlib.pyplot as plt

import torchvision.transforms as transforms
import torchvision.models as models

from torchvision.utils import save_image

import copy



device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

#imsize = 512 if torch.cuda.is_available() else 128  # use small size if no gpu
imsize = 512

loader = transforms.Compose([
    transforms.Resize(imsize),  # scale imported image
    transforms.ToTensor()])  # transform it into a torch tensor

unloader = transforms.ToPILImage()

def image_loader(image_name):
    image = Image.open(image_name)
    # fake batch dimension required to fit network's input dimensions
    image = loader(image).unsqueeze(0)
    return image.to(device, torch.float)



def imshow(tensor, title=None):
    image = tensor.cpu().clone()  # we clone the tensor to not do changes on it
    image = image.squeeze(0)      # remove the fake batch dimension
    image = unloader(image)
    plt.imshow(image)
    if title is not None:
        plt.title(title)
    plt.pause(0.001) # pause a bit so that plots are updated






class ContentLoss(nn.Module):

    def __init__(self, target,):
        super(ContentLoss, self).__init__()
        # we 'detach' the target content from the tree used
        # to dynamically compute the gradient: this is a stated value,
        # not a variable. Otherwise the forward method of the criterion
        # will throw an error.
        self.target = target.detach()

    def forward(self, input):
        self.loss = F.mse_loss(input, self.target)
        return input


def gram_matrix(input):
    a, b, c, d = input.size()  # a=batch size(=1)
    # b=number of feature maps
    # (c,d)=dimensions of a f. map (N=c*d)

    features = input.view(a * b, c * d)  # resise F_XL into \hat F_XL

    G = torch.mm(features, features.t())  # compute the gram product

    # we 'normalize' the values of the gram matrix
    # by dividing by the number of element in each feature maps.
    return G.div(a * b * c * d)


class StyleLoss(nn.Module):

    def __init__(self, target_feature):
        super(StyleLoss, self).__init__()
        self.target = gram_matrix(target_feature).detach()

    def forward(self, input):
        G = gram_matrix(input)
        self.loss = F.mse_loss(G, self.target)
        return input



# nn.Sequential
class Normalization(nn.Module):
    def __init__(self, mean, std):
        super(Normalization, self).__init__()
        # .view the mean and std to make them [C x 1 x 1] so that they can
        # directly work with image Tensor of shape [B x C x H x W].
        # B is batch size. C is number of channels. H is height and W is width.
        self.mean = torch.tensor(mean).view(-1, 1, 1)
        self.std = torch.tensor(std).view(-1, 1, 1)

    def forward(self, img):
        # normalize img
        return (img - self.mean) / self.std


def get_style_model_and_losses(cnn, normalization_mean, normalization_std,
                               style_img, content_img,
                               content_layers=['conv_5'],
                               style_layers=['conv_1','conv_2', 'conv_3', 'conv_4']):
    cnn = copy.deepcopy(cnn)

    # normalization module
    normalization = Normalization(normalization_mean, normalization_std).to(device)

    # just in order to have an iterable access to or list of content/syle
    # losses
    content_losses = []
    style_losses = []

    # assuming that cnn is a nn.Sequential, so we make a new nn.Sequential
    # to put in modules that are supposed to be activated sequentially
    model = nn.Sequential(normalization)

    i = 0  # increment every time we see a conv
    for layer in cnn.children():
        if isinstance(layer, nn.Conv2d):
            i += 1
            name = 'conv_{}'.format(i)

        elif isinstance(layer, nn.ReLU):
            name = 'relu_{}'.format(i)
            # The in-place version doesn't play very nicely with the ContentLoss
            # and StyleLoss we insert below. So we replace with out-of-place
            # ones here.
            layer = nn.ReLU(inplace=False)
        elif isinstance(layer, nn.MaxPool2d):
            name = 'pool_{}'.format(i)
            layer = nn.AvgPool2d(kernel_size=2,stride=2,padding=0,ceil_mode=False)
        elif isinstance(layer, nn.BatchNorm2d):
            name = 'bn_{}'.format(i)
        else:
            raise RuntimeError('Unrecognized layer: {}'.format(layer.__class__.__name__))

        model.add_module(name, layer)

        if name in content_layers:
            # add content loss:
            target = model(content_img).detach()
            content_loss = ContentLoss(target)
            model.add_module("content_loss_{}".format(i), content_loss)
            content_losses.append(content_loss)

        if name in style_layers:
            # add style loss:
            target_feature = model(style_img).detach()
            style_loss = StyleLoss(target_feature)
            model.add_module("style_loss_{}".format(i), style_loss)
            style_losses.append(style_loss)

    # now we trim off the layers after the last content and style losses
    for i in range(len(model) - 1, -1, -1):
        if isinstance(model[i], ContentLoss) or isinstance(model[i], StyleLoss):
            break

    model = model[:(i + 1)]

    return model, style_losses, content_losses






def get_input_optimizer(input_img):
    # this line to show that input is a parameter that requires a gradient
    optimizer = optim.LBFGS([input_img.requires_grad_()])
    return optimizer



def run_style_transfer(cnn, normalization_mean, normalization_std,
                       content_img, style_img, input_img, num_steps=300,
                       style_weight=1000000, content_weight=1,content_layers=['conv_5'],style_layers=['conv_1','conv_3','conv_4']):
    """Run the style transfer."""
    print('Building the style transfer model..')
    model, style_losses, content_losses = get_style_model_and_losses(cnn,
        normalization_mean, normalization_std, style_img, content_img, content_layers=content_layers,style_layers=style_layers)
    optimizer = get_input_optimizer(input_img)

    print('Optimizing..')
    run = [0]
    while run[0] <= num_steps:

        def closure():
            # correct the values of updated input image
            input_img.data.clamp_(0, 1)

            optimizer.zero_grad()
            model(input_img)
            style_score = 0
            content_score = 0

            for sl in style_losses:
                style_score += sl.loss
            for cl in content_losses:
                content_score += cl.loss

            style_score *= style_weight
            content_score *= content_weight

            loss = style_score + content_score
            loss.backward()

            run[0] += 1
            if run[0] % 10 == 0:
                print("run {}:".format(run))
                print('Style Loss : {:4f} Content Loss: {:4f}'.format(
                    style_score.item(), content_score.item()))
                print()

            if run[0] % 25==0:
            	save_image(input_img,'./images/step_'+str(run[0])+'.jpg')


            return style_score + content_score

        optimizer.step(closure)

    # a last correction...
    input_img.data.clamp_(0, 1)

    return input_img

def center_crop(img,size=512):
    x_center = int(img.shape[2] / 2.0)
    y_center = int(img.shape[3] / 2.0)
    return(img[:,:,x_center-int(size/2):x_center+int(size/2),y_center-int(size/2):y_center+int(size/2)])



def main():
	plt.ion()

	style_img = center_crop(image_loader("./styles/cubism.jpg"))
	content_img = center_crop(image_loader("./draghi/draghi_512.jpg"))

	assert style_img.size() == content_img.size(), \
	    "we need to import style and content images of the same size"

	unloader = transforms.ToPILImage()  # reconvert into PIL image

	cnn = models.vgg19(pretrained=True).features.to(device).eval()



	cnn_normalization_mean = torch.tensor([0.485, 0.456, 0.406]).to(device)
	cnn_normalization_std = torch.tensor([0.229, 0.224, 0.225]).to(device)

	# desired depth layers to compute style/content losses :
	content_layers = ['conv_16']
	style_layers = ['conv_1','conv_3','conv_9','conv_15']# 1, 3, 4, 9, are good

	preserve_colors = False

	#input_img = content_img.clone()


	# if you want to use white noise instead uncomment the below line:
	input_img = torch.randn(content_img.data.size(), device=device)

	# add the original input image to the figure:
	#plt.figure()
	#imshow(input_img, title='Input Image')

	if preserve_colors:
        style_img_flattened = style_img.flatten(start_dim=2,end_dim=3)[0]
        mean_style = style_img_flattened.mean(dim=1)
        style_img_flattened_centered = style_img_flattened -  torch.tensor(mean_style).view(-1,1)
        style_cov =np.cov(style_img_flattened_centered.numpy())
        w_style, v_style = np.linalg.eig(style_cov)
        style_cov_sqrt = v_style @ np.diag( w_style ** -0.5) @ v_style.T

        content_img_flattened = content_img.flatten(start_dim=2,end_dim=3)[0]
        mean_content = content_img_flattened.mean(dim=1)
        content_img_flattened_centered = content_img_flattened -  torch.tensor(mean_content).view(-1,1)
        content_cov =np.cov(content_img_flattened_centered.numpy())
        w_content, v_content = np.linalg.eig(content_cov)
        content_cov_sqrt = v_content @ np.diag(w_content ** 0.5) @ v_content .T

        transformation = content_cov_sqrt @ style_cov_sqrt

        new_style_flattened = (transformation @ style_img_flattened)

        adjustment = content_img_flattened.mean(dim=1).numpy() - new_style_flattened.mean(dim=1).numpy()

        new_style_flattened_shifted = new_style_flattened + (0.5*torch.tensor(adjustment).view(-1,1))

        new_style = torch.from_numpy(np.array([new_style_flattened_shifted.view(3,512,512).numpy()])).to(device,torch.float)

        style_img = new_style





	output = run_style_transfer(cnn, cnn_normalization_mean, cnn_normalization_std,
                            content_img, style_img, input_img=input_img, content_layers = content_layers,style_layers = style_layers,
                            num_steps=3e4,style_weight=1e6)

	plt.figure()
	imshow(output, title='Output Image')

	# sphinx_gallery_thumbnail_number = 4
	plt.ioff()
	plt.show()
	plt.savefig('./output.jpg')




