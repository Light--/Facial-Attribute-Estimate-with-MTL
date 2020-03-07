import torch
import torch.nn as nn
from .cbam import *
# if torch.cuda.is_available():
#     torch.set_default_tensor_type('torch.cuda.FloatTensor')

def conv_3x3_bn(inp, oup, stride=1):
    return nn.Sequential(nn.Conv2d(inp, oup, 3, stride, 1, bias=False),
                         nn.BatchNorm2d(oup), nn.ReLU6(inplace=True))

def conv_1x1_bn(inp, oup):
    return nn.Sequential(
        nn.Conv2d(inp, oup, 1, 1, 0, bias=False),
        nn.BatchNorm2d(oup),
        nn.ReLU6(inplace=True)
    )
def conv_3x3_bn_prelu(inp, oup, stride=1):
    return nn.Sequential(nn.Conv2d(inp, oup, 3, stride, 1, bias=False),
                         nn.BatchNorm2d(oup), nn.PReLU())


def conv_1x1_bn_prelu(inp, oup):
    return nn.Sequential(nn.Conv2d(inp, oup, 1, 1, 0, bias=False),
                         nn.BatchNorm2d(oup), nn.PReLU())

class psnet(nn.Module):
    def __init__(self, use_1x1=True, prelu='no',ratio=0.25, num_attributes=40, input_size=224):
        super().__init__()
        self.use_1x1 = use_1x1
        self.pool = nn.MaxPool2d(2, 2)
        if prelu == 'yes':
            conv3 = conv_3x3_bn_prelu
            conv1 = conv_1x1_bn_prelu
        else:
            conv3 = conv_3x3_bn
            conv1 = conv_1x1_bn

        self.t_conv = nn.ModuleList()  # (4,5),每一行是一个t支路的5个卷积层
        self.s_conv = nn.ModuleList([
            conv3(3, 32),
            conv3(160, 64),
            conv3(192, 128),
            conv3(256, 256),
            conv3(384, 128)
        ])  # (5,),s支路的5个卷积层
        self.t_fc = nn.ModuleList()  # (4,2)，每一行是一个t支路的2个FC层
        self.s_fc = nn.ModuleList([nn.Linear(3840, 512),
                                   nn.Linear(512, 512)])  # (2,)，s支路的2个FC层
        self.output = []  # (4,), 4个支路的输出
        self.conv_1x1 = nn.ModuleList() # (4,4)，用1x1卷积进行降维，取代原来的取前32个通道
        for _ in range(4):
            tmp = nn.ModuleList([
                conv1(32, 32),
                conv1(64, 32),
                conv1(128, 32),
                conv1(256, 32)
            ])
            self.conv_1x1.append(tmp)
        for _ in range(4):
            tmp = nn.ModuleList([
                conv3(3, 32),
                conv3(64, 64),
                conv3(128, 128),
                conv3(256, 256),
                conv3(512, 128)
            ])
            self.t_conv.append(tmp)

        for _ in range(4):
            tmp = nn.ModuleList([nn.Linear(3840, 512), nn.Linear(512, 512)])
            self.t_fc.append(tmp)


        self.group = nn.ModuleList([
            nn.Linear(1024, 26),
            nn.Linear(1024, 12),
            nn.Linear(1024, 18),
            nn.Linear(1024, 24)
        ])

        self.cbam_list = nn.ModuleList()  # (4,4), 仅在t支路上加入cbam模块
        for _ in range(4):
            tmp = nn.ModuleList(
                [CBAM(64),
                 CBAM(128),
                 CBAM(256),
                 CBAM(512)])
            self.cbam_list.append(tmp)

        self.avg_pool = nn.AdaptiveAvgPool2d(1)


    def forward(self, input):
        # print('input size ', input.size())
        self.output = []
        block_1, s_1 = self.block([input, input, input, input], input, 0)
        for i in range(4):
            block_1[i] = self.cbam_list[i][0](block_1[i])

        block_2, s_2 = self.block(block_1, s_1, 1)
        for i in range(4):
            block_2[i] = self.cbam_list[i][1](block_2[i])

        block_3, s_3 = self.block(block_2, s_2, 2)
        for i in range(4):
            block_3[i] = self.cbam_list[i][2](block_3[i])

        block_4, s_4 = self.block(block_3, s_3, 3)
        for i in range(4):
            block_4[i] = self.cbam_list[i][3](block_4[i])

        block_5, s_5 = self.block(block_4, s_4, 4)

        for i in range(4):
            _size = block_5[i].size()
            block_5[i] = block_5[i].view(-1, _size[1] * _size[2] * _size[3])
        s_5 = s_5.view(-1, s_5.size()[1] * s_5.size()[2] * s_5.size()[3])

        for i in range(4):
            block_5[i] = self.t_fc[i][0](block_5[i])
        s_0_fc1 = self.s_fc[0](s_5)

        for i in range(4):
            block_5[i] = self.t_fc[i][1](block_5[i])
        s_0_fc2 = self.s_fc[1](s_0_fc1)

        for i in range(4):
            self.output.append(torch.cat([block_5[i], s_0_fc2], 1))
        output_4 = torch.cat([s_0_fc2, s_0_fc2], 1)
        for i in range(4):
            self.output[i] = self.group[i](self.output[i])
        # a=torch.cat()
        output_0, output_1, output_2, output_3 = self.output
        return output_0, output_1, output_2, output_3
        # logit = torch.cat([output_0, output_1, output_2, output_3], 1)

        # return logit

    def block(self, inp, s_0, ind):
        t_0, t_1, t_2, t_3 = inp
        t_0 = self.t_conv[0][ind](t_0)
        t_1 = self.t_conv[1][ind](t_1)
        t_2 = self.t_conv[2][ind](t_2)
        t_3 = self.t_conv[3][ind](t_3)

        t_0 = self.pool(t_0)
        t_1 = self.pool(t_1)
        t_2 = self.pool(t_2)
        t_3 = self.pool(t_3)

        s_0 = self.s_conv[ind](s_0)
        s_0 = self.pool(s_0)

        if ind < 4:
            if not self.use_1x1:
                indices = torch.arange(0, 32, 1).cuda()
                t_0_partial = torch.index_select(t_0, 1, indices).cuda()
                t_1_partial = torch.index_select(t_1, 1, indices).cuda()
                t_2_partial = torch.index_select(t_2, 1, indices).cuda()
                t_3_partial = torch.index_select(t_3, 1, indices).cuda()

                t_0 = torch.cat([t_0, s_0], 1)
                t_1 = torch.cat([t_1, s_0], 1)
                t_2 = torch.cat([t_2, s_0], 1)
                t_3 = torch.cat([t_3, s_0], 1)

                s_0 = torch.cat(
                    [t_0_partial, t_1_partial, t_2_partial, t_3_partial, s_0], 1)
            else:
                t_0_1x1 = self.conv_1x1[0][ind](t_0)
                t_1_1x1 = self.conv_1x1[1][ind](t_1)
                t_2_1x1 = self.conv_1x1[2][ind](t_2)
                t_3_1x1 = self.conv_1x1[3][ind](t_3)

                t_0 = torch.cat([t_0, s_0], 1)
                t_1 = torch.cat([t_1, s_0], 1)
                t_2 = torch.cat([t_2, s_0], 1)
                t_3 = torch.cat([t_3, s_0], 1)     

                s_0 = torch.cat([t_0_1x1, t_1_1x1, t_2_1x1, t_3_1x1, s_0], 1)
        return [t_0, t_1, t_2, t_3], s_0
