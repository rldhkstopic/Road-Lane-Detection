from __future__ import division
from torch.autograd import Variable
from darknet import Darknet
from moviepy.editor import VideoFileClip
from scipy.misc import imresize

import numpy as np
import os
import io
import cv2
import math
import pickle
import time

from util import *

# Color
red = (0, 0, 255)
green = (0, 255, 0)
blue = (255, 0, 0)
white = (255, 255, 255)
yellow = (0, 255, 255)
deepgray = (43, 43, 43)
dark = (1, 1, 1)

cyan = (255, 255, 0)
magenta = (255, 0, 255)
lime = (0, 255, 128)
font = cv2.FONT_HERSHEY_SIMPLEX
font2 = cv2.FONT_HERSHEY_PLAIN

# Global 함수 초기화
l_center, r_center, lane_center = ((0,0)), ((0,0)), ((0,0))
pts = np.array([[0, 0], [0, 0], [0, 0], [0, 0]], np.int32)
pts = pts.reshape((-1, 1, 2))

def save_video(filename):
    fourcc = cv2.VideoWriter_fourcc(*'DIVX')
    out = cv2.VideoWriter(filename, fourcc, 60.0, (1280,720))
    return out

def grayscale(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

def canny(img, low_threshold, high_threshold):
    return cv2.Canny(img, low_threshold, high_threshold)

def gaussian_blur(img, kernel_size):
    return cv2.GaussianBlur(img, (kernel_size, kernel_size), 0)

def region_of_interest(img, vertices):
        mask = np.zeros_like(img)

        if len(img.shape) > 2:
            channel_count = img.shape[2]
            ignore_mask_color = (255, ) * channel_count
        else:
            ignore_mask_color = 255

        cv2.fillPoly(mask, vertices, ignore_mask_color)
        # vertiecs로 만든 polygon으로 이미지의 ROI를 정하고 ROI 이외의 영역은 모두 검정색으로 정한다.

        masked_image = cv2.bitwise_and(img, mask)
        return masked_image

def get_slope(x1,y1,x2,y2):
    return (y2-y1)/(x2-x1)

def draw_lines(img, lines):
    global cache
    global first_frame

    y_global_min = img.shape[0] # min은 y값 중 가장 큰값을 의미할 것이다. 또는 차로부터 멀어져 길을 따라 내려가는 지점 (?)
    y_max = img.shape[0]

    l_slope, r_slope = [], []
    l_lane, r_lane = [], []

    det_slope = 0.5
    margin = 10
    α = 0.2

    if lines is not None:
        for line in lines:
            for x1,y1,x2,y2 in line:
                slope = get_slope(x1,y1,x2,y2)
                if slope > det_slope:
                    r_slope.append(slope)
                    r_lane.append(line)
                elif slope < -det_slope:
                    l_slope.append(slope)
                    l_lane.append(line)

        y_global_min = min(y1, y2, y_global_min)

    if (len(l_lane) == 0 or len(r_lane) == 0): # 오류 방지
        return 1

    l_slope_mean = np.mean(l_slope, axis =0)
    r_slope_mean = np.mean(r_slope, axis =0)
    l_mean = np.mean(np.array(l_lane), axis=0)
    r_mean = np.mean(np.array(r_lane), axis=0)

    if ((r_slope_mean == 0) or (l_slope_mean == 0 )):
        print('dividing by zero')
        return 1

    # y=mx+b -> b = y -mx
    l_b = l_mean[0][1] - (l_slope_mean * l_mean[0][0])
    r_b = r_mean[0][1] - (r_slope_mean * r_mean[0][0])

    if np.isnan((y_global_min - l_b)/l_slope_mean) or \
    np.isnan((y_max - l_b)/l_slope_mean) or \
    np.isnan((y_global_min - r_b)/r_slope_mean) or \
    np.isnan((y_max - r_b)/r_slope_mean):
        return 1

    l_x1 = int((y_global_min - l_b)/l_slope_mean)
    l_x2 = int((y_max - l_b)/l_slope_mean)
    r_x1 = int((y_global_min - r_b)/r_slope_mean)
    r_x2 = int((y_max - r_b)/r_slope_mean)

    if l_x1 > r_x1: # Left line이 Right Line보다 오른쪽에 있는 경우 (Error)
        l_x1 = ((l_x1 + r_x1)/2)
        r_x1 = l_x1

        l_y1 = ((l_slope_mean * l_x1 ) + l_b)
        r_y1 = ((r_slope_mean * r_x1 ) + r_b)
        l_y2 = ((l_slope_mean * l_x2 ) + l_b)
        r_y2 = ((r_slope_mean * r_x2 ) + r_b)

    else: # l_x1 < r_x1 (Normal)
        l_y1 = y_global_min
        l_y2 = y_max
        r_y1 = y_global_min
        r_y2 = y_max

    current_frame = np.array([l_x1, l_y1, l_x2, l_y2, r_x1, r_y1, r_x2, r_y2], dtype ="float32")

    if first_frame == 1:
        next_frame = current_frame
        first_frame = 0
    else:
        prev_frame = cache
        next_frame = (1-α)*prev_frame+α*current_frame

    global pts
    pts = np.array([[next_frame[0], next_frame[1]], [next_frame[2], next_frame[3]], [next_frame[6], next_frame[7]], [next_frame[4], next_frame[5]]], np.int32)
    pts = pts.reshape((-1, 1, 2))

    global l_center
    global r_center
    global lane_center

    div = 2
    l_center = (int((next_frame[0] + next_frame[2]) / div), int((next_frame[1] + next_frame[3]) / div))
    r_center = (int((next_frame[4] + next_frame[6]) / div), int((next_frame[5] + next_frame[7]) / div))
    lane_center = (int((l_center[0] + r_center[0]) / div), int((l_center[1] + r_center[1]) / div))

    cache = next_frame

def hough_lines(img, rho, theta, threshold, min_line_len, max_line_gap):
    lines = cv2.HoughLinesP(img, rho, theta, threshold, np.array([]), minLineLength=min_line_len, maxLineGap=max_line_gap)
    line_img = np.zeros((img.shape[0], img.shape[1], 3), dtype=np.uint8)
    draw_lines(line_img, lines)
    return line_img

def weighted_img(img, initial_img, α=0.8, β=1., λ=0.):
    return cv2.addWeighted(initial_img, α, img, β, λ)

""" Processing Image """
def process_image(image):
    global first_frame

    image = imresize(image, (720, 1280, 3))
    height, width = image.shape[:2]

    kernel_size = 5

    # Canny Edge Detection Threshold
    low_thresh = 100
    high_thresh = 150

    rho = 4
    theta = np.pi/180
    thresh = 100
    min_line_len = 50
    max_line_gap = 150

    gray_image = grayscale(image)
    img_hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV) # 더 넓은 폭의 노란색 범위를 얻기위해 HSV를 이용한다.

    lower_yellow = np.array([20, 100, 100], dtype = "uint8")
    upper_yellow = np.array([30, 255, 255], dtype = "uint8")

    mask_yellow = cv2.inRange(img_hsv, lower_yellow, upper_yellow)
    mask_white = cv2.inRange(gray_image, 120, 255)

    mask_yw = cv2.bitwise_or(mask_white, mask_yellow) # 흰색과 노란색의 영역을 합친다.
    mask_yw_image = cv2.bitwise_and(gray_image, mask_yw) # Grayscale로 변환한 원본 이미지에서 흰색과 노란색만 추출

    gauss_gray = gaussian_blur(mask_yw_image, kernel_size)

    canny_edges = canny(gauss_gray, low_thresh, high_thresh)

    vertices = [get_pts(image)]
    roi_image = region_of_interest(canny_edges, vertices)

    line_image = hough_lines(roi_image, rho, theta, thresh, min_line_len, max_line_gap)
    result = weighted_img(line_image, image, α=0.8, β=1., λ=0.)
    # mask = cv2.polylines(result, vertices, True, (0, 255, 255)) # ROI mask

    return result

def get_pts(image):
    height, width = image.shape[:2]

    vertices = np.array([
                [250, 650],
                [550, 470],
                [730, 470],
                [1100, 650]
                ])
    return vertices

def get_roi(image):
    vertices = [get_pts(image)]
    roi_image = region_of_interest(canny_edges, vertices)
    return roi_image

hei = 30
font_size = 1
def warning_text(image, flag): # Information Box
    # cv2.rectangle(image, (0,0), (400, 250), deepgray, -1)
    if flag == 0:
        cv2.putText(image, 'WARNING : ', (10, hei*2), font, 1, white, font_size)
        cv2.putText(image, 'None', (190, hei*2), font, 1, white, font_size)
    elif flag == 1:
        cv2.putText(image, 'WARNING : ', (10, hei*2), font, 1, red, font_size)
        cv2.putText(image, 'Turn Right', (190, hei*2), font, 1, red, font_size)
    elif flag == 2:
        cv2.putText(image, 'WARNING : ', (10, hei*2), font, 1, red, font_size)
        cv2.putText(image, 'Turn Left', (190, hei*2), font, 1, red, font_size)

def show_fps(image, frames, start, color = white):
    now_fps = round(frames / (time.time() - start), 2)
    cv2.putText(image, "FPS : %.2f"%now_fps, (10, hei), font, 1, color, font_size)

def std_line(image, height, whalf, color = yellow):
    cv2.line(image, (whalf, lane_center[1]), (whalf, int(height)), color, 2)
    cv2.line(image, (whalf, lane_center[1]), (lane_center[0], lane_center[1]), color, 2)

def warning_line(image, whalf, gap=20, length=10, color = white):
    cv2.line(image, (whalf-gap, lane_center[1]-length), (whalf-gap, lane_center[1]+length), color, 1)
    cv2.line(image, (whalf+gap, lane_center[1]-length), (whalf+gap, lane_center[1]+length), color, 1)

def lane_position(image, length=30, thickness=3, color = red):
    cv2.line(image, (l_center[0], l_center[1]), (l_center[0], l_center[1]-length), color, thickness)
    cv2.line(image, (r_center[0], r_center[1]), (r_center[0], r_center[1]-length), color, thickness)
    cv2.line(image, (lane_center[0], lane_center[1]), (lane_center[0], lane_center[1]-length), color, thickness)

#####################
def visualize(image):
    height, width = image.shape[:2]
    zeros = np.zeros_like(image)
    whalf = int(width/2)
    hhalf = int(height/2)

    flag = 0
    if not lane_center[1] < hhalf:
        gap = 20
        max = 200
        if r_center[0]-l_center[0] > max:
            cv2.fillPoly(zeros, [pts], lime)
            std_line(zeros, height = height, whalf = whalf)# Standard Line
            warning_line(zeros, gap = gap, whalf = whalf) # Warning Boundary
            lane_position(zeros) # Lane Position

            if lane_center[0] < whalf-gap:
                flag = 1
            elif lane_center[0] > whalf+gap:
                flag = 2
            else :
                flag = 0

    warning_text(zeros, flag)
    return zeros


######################
def write(x, results, color = [126, 232, 229], font_color = dark):
    c1 = tuple(x[1:3].int())
    c2 = tuple(x[3:5].int())
    cls = int(x[-1])

    image = results
    label = "{0}".format(classes[cls])
    if not abs(c1[0]-c2[0]) > 1000:
        cv2.rectangle(image, c1, c2, color, 1)

    t_size = cv2.getTextSize(label, font2, 1 , 1)[0]
    c2 = c1[0] + t_size[0] + 3, c1[1] + t_size[1] + 4

    cv2.rectangle(image, c1, c2, color, -1)
    cv2.putText(image, label, (c1[0], c1[1] + t_size[1] + 4), font2, 1, font_color, 1);
    return image


"""------------------------Data Directory------------------------------------"""
cfg = "cfg/yolov3.cfg"
weights = "weights/yolov3.weights"
names = "data/coco.names"

image_name = "Drive.mp4"
image_directory = "test_videos/"
video = image_directory + image_name


"""--------------------------Video test--------------------------------------"""
start = 0
batch_size = 1
confidence = 0.5
nms_thesh = 0.4
resol = 416 # 해상도

num_classes = 80
print("Reading <configure> file..")
model = Darknet(cfg)
print("Reading < weights > file..")
model.load_weights(weights)
print("Reading < classes > file..")
classes = load_classes(names)

print("\nNetwork successfully loaded!")

model.net_info["height"] = resol
input_dim = int(model.net_info["height"])
assert input_dim % 32 == 0, "Please, Set the Input resolution which can divide into 32 parts"
assert input_dim > 32, "Please, Set the Input resolution to above 32"

CUDA = torch.cuda.is_available()
if CUDA:
    model.cuda()
model.eval()

frames = 0
first_frame = 1
start = time.time()

cap = cv2.VideoCapture(video)
clip1 = save_video('out_videos/result2_' + image_name) # result 영상 저장
while (cap.isOpened()):
    ret, frame = cap.read()
    if ret:
        # Lane Detection and Region of Lane mask
        cv2.rectangle(frame, (0,0), (400, 130), dark, -1)
        cpframe = frame.copy()
        prc_img = process_image(cpframe)
        lane_detection = visualize(prc_img)

        # YOLO, Object Detection
        # prep_frame = prep_image(frame, input_dim)
        # frame_dim = frame.shape[1], frame.shape[0]
        # frame_dim = torch.FloatTensor(frame_dim).repeat(1, 2)
        #
        # if CUDA:
        #     frame_dim = frame_dim.cuda()
        #     prep_frame = prep_frame.cuda()
        #
        # with torch.no_grad():
        #     output = model(Variable(prep_frame, True), CUDA)
        # output = write_results(output, confidence, num_classes, nms_thesh)
        #
        # if type(output) == int:
        #     frames += 1
        #     cv2.imshow("Frame", frame)
        #
        #     key = cv2.waitKey(1)
        #     if key & 0xFF == ord('q'):
        #         break
        #     continue
        #
        # frame_dim = frame_dim.repeat(output.size(0), 1)
        # scaling_factor = torch.min(416/frame_dim, 1)[0].view(-1, 1)
        #
        # output[:, [1, 3]] -= (input_dim - scaling_factor * frame_dim[:, 0].view(-1, 1))/2
        # output[:, [2, 4]] -= (input_dim - scaling_factor * frame_dim[:, 1].view(-1, 1))/2
        # output[:, 1 : 5] /= scaling_factor
        #
        # for i in range(output.shape[0]):
        #     output[i, [1,3]] = torch.clamp(output[i, [1,3]], 0.0, frame_dim[i,0])
        #     output[i, [2,4]] = torch.clamp(output[i, [2,4]], 0.0, frame_dim[i,1])
        #
        # list(map(lambda x: write(x, frame), output))

        result = cv2.addWeighted(frame, 1, lane_detection, 0.7, 0)
        show_fps(result, frames, start, color = yellow)

        cv2.imshow("Result", result)
        # clip1.write(result)
        frames += 1
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

cap.release()
cv2.destroyAllWindows()
