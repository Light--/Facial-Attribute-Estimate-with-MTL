'''
Training script for CelebA
Copyright (c) Ke Xu, 2020
'''

import argparse
import os
import shutil
import time
import random
import math

import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.distributed as dist
import torch.optim
import torch.utils.data
import torch.utils.data.distributed
import torchvision.transforms as transforms
import torchvision.datasets as datasets
import torch.nn.functional as F
import models
from math import cos, pi

# from prefetch_generator import BackgroundGenerator
from celeba import CelebA, TensorSampler, data_prefetcher
from utils import Bar, Logger, AverageMeter, accuracy, mkdir_p, savefig
from tensorboardX import SummaryWriter

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
parser = argparse.ArgumentParser(description='PyTorch CelebA Training')
parser.add_argument('-d',
                    '--data',
                    default='/root/OneDrive/DataSets/CelebA/Anno',
                    type=str)
parser.add_argument('-j',
                    '--workers',
                    default=4,
                    type=int,
                    help='number of data loading workers (default: 4)')
# Optimization options
parser.add_argument('--epochs',
                    default=20,
                    type=int,
                    help='number of total epochs to run')
parser.add_argument('--start-epoch',
                    default=0,
                    type=int,
                    help='manual epoch number (useful on restarts)')
parser.add_argument('--train-batch',
                    default=32,
                    type=int,
                    help='train batchsize (default: 256)')
parser.add_argument('--test-batch',
                    default=32,
                    type=int,
                    help='test batchsize (default: 200)')
parser.add_argument('--lr',
                    '--learning-rate',
                    default=1e-3,
                    type=float,
                    help='initial learning rate')
parser.add_argument('--lr-decay',
                    type=str,
                    default='step',
                    help='mode for learning rate decay')
parser.add_argument('--step',
                    type=int,
                    default=10,
                    help='interval for learning rate decay in step mode')
parser.add_argument('--schedule',
                    type=int,
                    nargs='+',
                    default=[15, 25],
                    help='decrease learning rate at these epochs.')
parser.add_argument('--turning-point',
                    type=int,
                    default=100,
                    help='epoch number from linear to exponential decay mode')
parser.add_argument('--gamma',
                    type=float,
                    default=0.1,
                    help='LR is multiplied by gamma on schedule.')
parser.add_argument('--momentum',
                    default=0.9,
                    type=float,
                    metavar='M',
                    help='momentum')
parser.add_argument('--weight-decay',
                    '--wd',
                    default=1e-4,
                    type=float,
                    metavar='W',
                    help='weight decay (default: 1e-4)')
# Checkpoints
parser.add_argument('-c',
                    '--checkpoint',
                    default='checkpoints_' +
                    time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime()),
                    type=str,
                    help='path to save checkpoint (default: checkpoints)')
parser.add_argument('--resume',
                    default='',
                    type=str,
                    help='path to latest checkpoint (default: none)')
# Miscs
parser.add_argument('--manual-seed', type=int, help='manual seed')
parser.add_argument('-e',
                    '--evaluate',
                    dest='evaluate',
                    action='store_true',
                    help='evaluate model on validation set')
parser.add_argument('--pretrained',
                    dest='pretrained',
                    action='store_true',
                    help='use pre-trained model')
# Device options
parser.add_argument('--gpu-id',
                    default='0',
                    type=str,
                    help='id(s) for CUDA_VISIBLE_DEVICES')
parser.add_argument('--version',
                    default=1,
                    type=int,
                    help='The version of ps-mcnn architecture')
parser.add_argument('--place',
                    default='deepai',
                    type=str,
                    help='The place where the programm on')
best_prec1 = 0

label_list = [
    'Arched_Eyebrows', 'Bags_Under_Eyes', 'Bald', 'Bangs', 'Black_Hair',
    'Blond_Hair', 'Brown_Hair', 'Bushy_Eyebrows', 'Eyeglasses', 'Gray_Hair',
    'Narrow_Eyes', 'Receding_Hairline', 'Wearing_Hat', 'Big_Nose',
    'High_Cheekbones', 'Pointy_Nose', 'Rosy_Cheeks', 'Sideburns',
    'Wearing_Earrings', 'Big_Lips', 'Double_Chin', 'Goatee', 'Mustache',
    'Mouth_Slightly_Open', 'No_Beard', 'Wearing_Lipstick', 'Wearing_Necklace',
    'Wearing_Necktie', '5_o_Clock_Shadow', 'Attractive', 'Blurry', 'Chubby',
    'Heavy_Makeup', 'Male', 'Oval_Face', 'Pale_Skin', 'Straight_Hair',
    'Smiling', 'Wavy_Hair', 'Young'
]
attr_order = [
    1, 3, 4, 5, 8, 9, 11, 12, 15, 17, 23, 28, 35, 7, 19, 27, 29, 30, 34, 6, 14,
    16, 22, 21, 24, 36, 37, 38, 0, 2, 10, 13, 18, 20, 25, 26, 32, 31, 33, 39
]


def main():
    global args, best_prec1
    args = parser.parse_args()

    # Use CUDA
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu_id
    use_cuda = torch.cuda.is_available()

    # Random seed
    if args.manual_seed is None:
        args.manual_seed = random.randint(1, 10000)
    random.seed(args.manual_seed)
    torch.manual_seed(args.manual_seed)
    if use_cuda:
        torch.cuda.manual_seed_all(args.manual_seed)

    # create model
    if args.version == 1:
        model = models.psmcnn_se_1.psnet().to(device)
    elif args.version == 2:
        model = models.psmcnn_se_2.psnet().to(device)
    else:
        model = models.psmcnn_se_3.psnet().to(device)

    data_path = ''
    if args.place == 'deepai':
        data_path = '/root/OneDrive/DataSets/CelebA/'
    elif args.place == 'my':
        data_path = '/media/xuke/SoftWare/BaiduNetdiskDownload/CelebA/'
    elif args.place == 'kb541':
        data_path = ''
    elif args.place == 'phd-1':
        data_path = '/media/kb541/data/xuke/CelebA/'
    # model.apply(weight_init)
    # define loss function (criterion) and optimizer
    criterion = nn.CrossEntropyLoss().cuda()

    # optimizer = torch.optim.SGD(model.parameters(),
    #                             args.lr,
    #                             momentum=args.momentum,
    #                             weight_decay=args.weight_decay)
    optimizer = torch.optim.Adam(model.parameters(),
                                 args.lr,
                                 weight_decay=args.weight_decay)
    # optionally resume from a checkpoint
    title = 'CelebA-psmcnn'
    if not os.path.isdir(args.checkpoint):
        mkdir_p(args.checkpoint)

    # resume work
    if args.resume:
        if os.path.isfile(args.resume):
            print("=> loading checkpoint '{}'".format(args.resume))
            checkpoint = torch.load(args.resume)
            args.start_epoch = checkpoint['epoch']
            best_prec1 = checkpoint['best_prec1']
            model.load_state_dict(checkpoint['state_dict'])
            optimizer.load_state_dict(checkpoint['optimizer'])
            print("=> loaded checkpoint '{}' (epoch {})".format(
                args.resume, checkpoint['epoch']))
            args.checkpoint = os.path.dirname(args.resume)
            logger = Logger(os.path.join(args.checkpoint, 'log.txt'),
                            title=title,
                            resume=True)
        else:
            print("=> no checkpoint found at '{}'".format(args.resume))
    else:
        logger = Logger(os.path.join(args.checkpoint, 'log.txt'), title=title)
        logger.set_names([
            'Learning Rate', 'Train Loss', 'Valid Loss', 'Train Acc.',
            'Valid Acc.'
        ])

    cudnn.benchmark = True

    # Data loading code
    # normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    normalize = transforms.Normalize(mean=[0.383, 0.426, 0.506],
                                     std=[0.290, 0.290, 0.311])

    train_dataset = CelebA(
        data_path, 'list_attr_celeba_train.txt', 'identity_CelebA_train.txt',
        transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.RandomHorizontalFlip(),
            transforms.RandomCrop((160, 192)),
            transforms.ToTensor(),
            normalize,
        ]))
    val_dataset = CelebA(
        data_path, 'list_attr_celeba_val.txt', 'identity_CelebA_val.txt',
        transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.RandomCrop((160, 192)),
            transforms.ToTensor(),
            normalize,
        ]))
    test_dataset = CelebA(
        data_path, 'list_attr_celeba_test.txt', 'identity_CelebA_test.txt',
        transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.RandomCrop((160, 192)),
            transforms.ToTensor(),
            normalize,
        ]))

    train_loader = torch.utils.data.DataLoader(train_dataset,
                                               batch_size=args.train_batch,
                                               shuffle=True,
                                               num_workers=args.workers,
                                               pin_memory=False)

    val_loader = torch.utils.data.DataLoader(val_dataset,
                                             batch_size=args.test_batch,
                                             num_workers=args.workers,
                                             shuffle=False,
                                             pin_memory=False)

    test_loader = torch.utils.data.DataLoader(test_dataset,
                                              batch_size=args.test_batch,
                                              num_workers=args.workers,
                                              pin_memory=False)

    # if args.evaluate:
    #     validate(test_loader, model, criterion,)
    #     return

    # visualization
    writer = SummaryWriter(os.path.join(args.checkpoint, 'logs'))
    count_train = 0
    count_val = 0
    each_train_best = [0] * 40
    each_val_best = [0] * 40
    each_train = torch.zeros(40, device='cuda:0')
    each_val = torch.zeros(40, device='cuda:0')
    best_train = {}
    best_val = {}
    for epoch in range(args.start_epoch, args.epochs):
        lr = adjust_learning_rate(optimizer, epoch)

        print('\nEpoch: [%d | %d] LR: %f' % (epoch + 1, args.epochs, lr))

        # train for one epoch
        train_loss, train_acc, each_train, count_train = train(
            train_loader, model, criterion, optimizer, epoch, writer,
            count_train, each_train)

        # evaluate on validation set
        val_loss, prec1, each_val, count_val = validate(
            val_loader, model, criterion, writer, count_val, each_val)

        # append logger file
        logger.append([lr, train_loss, val_loss, train_acc, prec1])

        # for i in range(40):
        #     each_train_best[i] = each_train_best[
        #         i] if each_train_best[i] > each_train[i] else each_train[i]
        #     each_val_best[i] = each_val_best[
        #         i] if each_val_best[i] > each_val[i] else each_val[i]
        #     best_train[label_list[i]] = each_train_best[i]
        #     best_val[label_list[i]] = each_val_best[i]
        # tensorboardX
        writer.add_scalar('learning_rate', lr, epoch + 1)
        writer.add_scalars('each_train', best_train, epoch + 1)
        writer.add_scalars('each_val', best_val, epoch + 1)
        is_best = prec1 > best_prec1
        best_prec1 = max(prec1, best_prec1)
        save_checkpoint(
            {
                'epoch': epoch + 1,
                'arch': 'psmcnn',
                'state_dict': model.state_dict(),
                'best_prec1': best_prec1,
                'optimizer': optimizer.state_dict(),
            },
            is_best,
            checkpoint=args.checkpoint)
    print('[*] Training task finished at ',
          time.strftime("%Y-%m-%d %H-%M-%S", time.localtime()))
    logger.close()
    logger.plot()
    savefig(os.path.join(args.checkpoint, 'log.eps'))
    writer.close()

    print('Best accuracy:')
    print(best_prec1)
    print('Best accuracy of each attribute:')
    for key, item in each_val.items():
        print(key, ':', item, end='   ===   ')


def train(train_loader, model, criterion, optimizer, epoch, writer, count,
          each_total):
    bar = Bar('Training', max=len(train_loader))
    batch_time = AverageMeter()
    data_time = AverageMeter()
    # switch to train mode
    model.train()

    end = time.time()
    train_total = 0.0
    train_correct = 0.0
    # count = 0
    weight = [1] * 40
    stage = 1
    if epoch >= 10:
        stage = 1
    for i, (input, target, id_target) in enumerate(train_loader):
        # 预加载代码，暂时不用，会爆显存
        # prefetcher = data_prefetcher(train_loader)
        # input, target = prefetcher.next()
        # i = 0
        # while input is not None:

        # measure data loading time
        optimizer.zero_grad()

        data_time.update(time.time() - end)

        input = input.cuda(non_blocking=True)
        target = target.cuda(non_blocking=True)
        id_target = id_target.cuda(non_blocking=True)

        # compute output
        output_0, output_1, output_2, output_3 = model.forward(input)
        output_0 = output_0.view(-1, 2, 13)
        output_1 = output_1.view(-1, 2, 6)
        output_2 = output_2.view(-1, 2, 9)
        output_3 = output_3.view(-1, 2, 12)
        output = torch.cat([output_0, output_1, output_2, output_3], 2)

        # measure accuracy and record loss
        loss = 0.0
        loss_attr = [0.0 for i in range(40)]
        for k in range(40):
            if stage == 0:
                loss_attr[k] += criterion(output[:, :, k], target[:, k].long())
            else:
                loss_attr[k] += criterion(output[:, :, k],
                                          target[:, k].long()) * weight[k]
            loss += loss_attr[k]

        # 加入LC-loss
        lc_loss = 0.0
        for u in range(len(id_target)):
            for v in range(u + 1, len(id_target)):
                if id_target[u] == id_target[v]:
                    lc_loss += torch.sum(
                        (output[u, :, :] - output[v, :, :])**2)
        lc_loss /= 1560  # N*(N-1)，本例中就是40*39=1560
        loss += lc_loss
        loss = loss.requires_grad_()
        _, pred = torch.max(output, 1)  # (?,40)

        # loss的加权
        max_loss = max(loss_attr)
        min_loss = min(loss_attr)
        avg_loss = sum(loss_attr) / len(loss_attr)
        for ii in range(40):
            weight[ii] = math.exp(
                (loss_attr[ii] - avg_loss) / (max_loss - min_loss))

        # 每个属性在当前batch的准确率
        correct_single = torch.sum(
            pred == target, 0, dtype=torch.float32) / output.size(0)  # (?,40)

        # 所有属性的平均准确率
        if i == 0:
            each_total = correct_single
        else:
            each_total = (each_total + correct_single) / 2

        train_correct += torch.sum(
            pred == target,
            dtype=torch.float32).item()  # num_classes need you to define

        train_total += output.size(0)
        cls_train_Accuracy = train_correct / train_total / 40.0
        loss_avg = sum(loss_attr) / len(loss_attr)

        loss.backward()
        optimizer.step()

        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        # 统计每个属性的准确率
        acc_dic = {'train_accuracy': cls_train_Accuracy}
        for ii in range(len(correct_single)):
            acc_dic[label_list[ii]] = correct_single[ii]
        writer.add_scalars('loss', {'train_loss': loss_avg}, count)
        writer.add_scalars('acc_train', acc_dic, count)
        count += 1
        # plot progress

        # Best and worst performance
        # best_acc_id = torch.argmax(correct_single)
        # best_attr = label_list[best_acc_id]
        # best_acc = correct_single[best_acc_id]

        # worst_acc_id = torch.argmin(correct_single)
        # worst_attr = label_list[worst_acc_id]
        # worst_acc = correct_single[worst_acc_id]
        bar.suffix = '({batch}/{size}) Data: {data:.3f}s | Batch: {bt:.3f}s | Total: {total:} | ETA: {eta:} | Loss: {loss:.4f} | '\
        'top1: {top1: .5f}'.format(
            batch=i + 1,
            size=len(train_loader),
            data=data_time.avg,
            bt=batch_time.avg,
            total=bar.elapsed_td,
            eta=bar.eta_td,
            loss=loss_avg,
            top1=cls_train_Accuracy
        )
        bar.next()

    # i += 1
    # input, target = prefetcher.next()
    bar.finish()
    return (loss_avg, cls_train_Accuracy, each_total, count)


def validate(val_loader, model, criterion, writer, count, each_total):
    bar = Bar('Validating', max=len(val_loader))

    batch_time = AverageMeter()
    data_time = AverageMeter()

    # switch to evaluate mode
    model.eval()

    with torch.no_grad():
        end = time.time()
        val_total = 0.0
        val_correct = 0.0
        for i, (input, target, id_target) in enumerate(val_loader):
            # measure data loading time
            data_time.update(time.time() - end)

            input = input.cuda(non_blocking=True)
            target = target.cuda(non_blocking=True)

            # compute output
            # output = model(input)
            output_0, output_1, output_2, output_3 = model.forward(input)
            output_0 = output_0.view(-1, 2, 13)
            output_1 = output_1.view(-1, 2, 6)
            output_2 = output_2.view(-1, 2, 9)
            output_3 = output_3.view(-1, 2, 12)
            output = torch.cat([output_0, output_1, output_2, output_3], 2)

            # measure accuracy and record loss
            loss = 0.0
            loss_attr = [0.0 for i in range(40)]
            for k in range(40):
                loss_attr[k] += criterion(output[:, :, k], target[:, k])
                loss += loss_attr[k]

            # 加入LC-loss
            lc_loss = 0.0
            for u in range(len(id_target)):
                for v in range(u + 1, len(id_target)):
                    if id_target[u] == id_target[v]:
                        lc_loss += torch.sum(
                            (output[u, :, :] - output[v, :, :])**2)
            lc_loss /= 1560  # N*(N-1)，本例中就是40*39=1560
            loss += lc_loss
            loss = loss.requires_grad_()
            _, pred = torch.max(output, 1)
            correct_single = torch.sum(pred == target, 0,
                                       dtype=torch.float32) / output.size(0)
            # 所有属性的平均准确率
            if i == 0:
                each_total = correct_single
            else:
                each_total = (each_total + correct_single) / 2

            val_correct += torch.sum(pred == target, dtype=torch.float32).item(
            ) / 40.0  # num_classes need you to define

            val_total += output.size(0)
            cls_val_Accuracy = val_correct / val_total
            loss_avg = sum(loss_attr) / len(loss_attr)
            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            acc_dic = {'validate_accuracy': cls_val_Accuracy}
            for ii in range(len(correct_single)):
                acc_dic[label_list[ii]] = correct_single[ii]
            writer.add_scalars('loss', {'validate_loss': loss_avg}, count)
            writer.add_scalars('acc_val', acc_dic, count)
            count += 1
            # plot progress
            # best_acc_id = torch.argmax(correct_single)
            # best_attr = label_list[best_acc_id]
            # best_acc = correct_single[best_acc_id]

            # worst_acc_id = torch.argmin(correct_single)
            # worst_attr = label_list[worst_acc_id]
            # worst_acc = correct_single[worst_acc_id]
            bar.suffix = '({batch}/{size}) Data: {data:.3f}s | Batch: {bt:.3f}s | Total: {total:} | ETA: {eta:} | Loss: {loss:.4f} | '\
                'top1: {top1: .5f}'.format(
                batch=i + 1,
                size=len(val_loader),
                data=data_time.avg,
                bt=batch_time.avg,
                total=bar.elapsed_td,
                eta=bar.eta_td,
                loss=loss_avg,
                top1=cls_val_Accuracy
            )
            bar.next()
    bar.finish()
    return (loss_avg, cls_val_Accuracy, each_total, count)


def save_checkpoint(state,
                    is_best,
                    checkpoint='checkpoint',
                    filename='checkpoint.pth.tar'):
    filepath = os.path.join(checkpoint, filename)
    torch.save(state, filepath)
    if is_best:
        shutil.copyfile(filepath, os.path.join(checkpoint,
                                               'model_best.pth.tar'))


def adjust_learning_rate(optimizer, epoch):
    lr = optimizer.param_groups[0]['lr']
    """Sets the learning rate to the initial LR decayed by 10 following schedule"""
    if args.lr_decay == 'step':
        lr = args.lr * (args.gamma**(epoch // args.step))
    elif args.lr_decay == 'cos':
        lr = args.lr * (1 + cos(pi * epoch / args.epochs)) / 2
    elif args.lr_decay == 'linear':
        lr = args.lr * (1 - epoch / args.epochs)
    elif args.lr_decay == 'linear2exp':
        if epoch < args.turning_point + 1:
            # learning rate decay as 95% at the turning point (1 / 95% = 1.0526)
            lr = args.lr * (1 - epoch / int(args.turning_point * 1.0526))
        else:
            lr *= args.gamma
    elif args.lr_decay == 'schedule':
        if epoch in args.schedule:
            lr *= args.gamma
    elif args.lr_decay=='warmup':
        if epoch<5:
            lr=1e-5
        else:
            if epoch in args.schedule:
                lr *= args.gamma            
    else:
        raise ValueError('Unknown lr mode {}'.format(args.lr_decay))
    
    if epoch==5:
        for param_group in optimizer.param_groups:
            param_group['lr'] = args.lr
    else:
        for param_group in optimizer.param_groups:
            param_group['lr'] = lr

    return lr


def weight_init(m):
    if isinstance(m, nn.Linear):
        nn.init.xavier_normal_(m.weight)
        nn.init.constant_(m.bias, 0)
    # 也可以判断是否为conv2d，使用相应的初始化方式
    elif isinstance(m, nn.Conv2d):
        nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
    # 是否为批归一化层
    elif isinstance(m, nn.BatchNorm2d):
        nn.init.constant_(m.weight, 1)
        nn.init.constant_(m.bias, 0)


if __name__ == '__main__':
    main()
