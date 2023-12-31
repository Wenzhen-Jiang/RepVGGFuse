import os
import time
import scipy.io as scio
import random
import torch
from torch.optim import Adam
from torch.autograd import Variable
import utils
from net import RepVGGFuse_net, Vgg19
from args import Args as args
from visdom import Visdom


# 加载指定路径下的图像文件路径，并返回一个包含图像文件路径的列表
def load_data(path, train_num):
    imgs_path, _ = utils.list_images(path)
    imgs_path = imgs_path[:train_num]
    random.shuffle(imgs_path)
    return imgs_path


def main():
    path = args.path_ir
    train_num = 20000
    data = load_data(path, train_num)
    if args.channel == 1:
        img_flag = False
    else:
        img_flag = True
    w_vi = 0.5
    lam2_vi = args.lam2_vi
    w_ir = args.w_ir
    lam3_gram = args.lam3_gram
    train(data, img_flag, lam2_vi, w_vi, w_ir, lam3_gram, )


def train(data, img_flag, lam2_vi, w_vi, w_ir, lam3_gram, ):
    batch_size = args.batch_size
    # load network model
    model = RepVGGFuse_net(args.s, args.n, args.channel, args.stride, deploy=False,
                           use_se=False)

    if args.resume is not None:
        print('Resuming, initializing using weight from {}.'.format(args.resume))
        model.load_state_dict(torch.load(args.resume))
    print(model)
    optimizer = Adam(model.parameters(), args.lr, weight_decay=0.9)
    mse_loss = torch.nn.MSELoss()

    # visdom
    viz = Visdom()

    # Loss network - VGG19
    vgg = Vgg19()
    utils.init_vgg19(vgg, os.path.join(args.vgg_model_dir, "vgg19.pth"))

    if args.cuda:
        model.cuda()
        vgg.cuda()

    print('Start training.....')

    # creating save path
    temp_path_model = os.path.join(args.save_model_dir)
    if os.path.exists(temp_path_model) is False:
        os.mkdir(temp_path_model)

    temp_path_loss = os.path.join(args.save_loss_dir)
    if os.path.exists(temp_path_loss) is False:
        os.mkdir(temp_path_loss)

    Loss_list1 = []
    Loss_list2 = []
    Loss_list3 = []
    Loss_list4 = []
    Loss_list5 = []
    Loss_list_all = []
    count = 0
    viz_index = 0
    loss_p1 = 0.
    loss_p2 = 0.
    loss_p3 = 0.
    loss_p4 = 0.
    loss_p5 = 0.
    loss_all = 0.
    start_time = time.time()
    model.train()
    for e in range(args.epochs):
        img_paths, batch_num = utils.load_dataset(data, batch_size)
        # 在每个 epoch 开始前记录当前时间
        epoch_start_time = time.time()

        for idx in range(batch_num):
            # print(idx)
            image_paths_ir = img_paths[idx * batch_size:(idx * batch_size + batch_size)]
            # print(image_paths_ir)
            img_ir = utils.get_train_images(image_paths_ir, height=args.Height, width=args.Width, flag=img_flag)

            image_paths_vi = [x.replace('IR', 'VIS') for x in image_paths_ir]
            img_vi = utils.get_train_images(image_paths_vi, height=args.Height, width=args.Width, flag=img_flag)
            # print(image_paths_ir)
            # print(image_paths_vi)
            count += 1
            optimizer.zero_grad()
            batch_ir = Variable(img_ir, requires_grad=False)
            batch_vi = Variable(img_vi, requires_grad=False)

            if args.cuda:
                batch_ir = batch_ir.cuda()
                batch_vi = batch_vi.cuda()
            # normalize for each batch
            batch_ir_in = utils.normalize_tensor(batch_ir)
            batch_vi_in = utils.normalize_tensor(batch_vi)
            # get fusion image
            outputs = model(batch_ir_in, batch_vi_in)
            out_f = utils.normalize_tensor(outputs)
            out_f = out_f * 255
            # ---------- LOSS FUNCTION ----------
            loss_pixel_vi = 10 * mse_loss(out_f, batch_vi)
            # --- Feature loss ----
            vgg_outs = vgg(out_f)
            vgg_irs = vgg(batch_ir)
            vgg_vis = vgg(batch_vi)

            t_idx = 0
            loss_fea_vi = 0.
            loss_fea_ir = 0.
            loss_gram_ir = 0.
            weights_fea = [lam2_vi, 0.01, 0.5, 0.1]
            weights_gram = [0, 0, 0.1, lam3_gram]
            # w_ir = 4.0
            # w_vi = 0.5
            for fea_out, fea_ir, fea_vi, w_fea, w_gram in zip(vgg_outs, vgg_irs, vgg_vis, weights_fea, weights_gram):
                if t_idx == 0:
                    loss_fea_vi = w_fea * mse_loss(fea_out, fea_vi)
                if t_idx == 1 or t_idx == 2:
                    # relu2_2, relu3_3, relu4_3
                    loss_fea_ir += w_fea * mse_loss(fea_out, w_ir * fea_ir + w_vi * fea_vi)
                if t_idx == 3:
                    gram_out = utils.gram_matrix(fea_out)
                    gram_ir = utils.gram_matrix(fea_ir)
                    loss_gram_ir += w_gram * mse_loss(gram_out, gram_ir)
                t_idx += 1

            # total loss
            total_loss = loss_pixel_vi + loss_fea_vi + loss_fea_ir + loss_gram_ir
            # total loss
            total_loss.backward()
            optimizer.step()

            loss_p1 += loss_pixel_vi
            loss_p2 += loss_fea_vi
            loss_p3 += loss_fea_ir
            loss_p4 += loss_gram_ir
            loss_all += total_loss

            step = 10
            if count % step == 0:
                loss_p1 /= step
                loss_p2 /= step
                loss_p3 /= step
                loss_p4 /= step
                loss_p5 /= step
                loss_all /= step
                if e == 0 and count == step:
                    viz.line([loss_all.item()], [0.], win='total_loss', opts=dict(title='Total Loss'))
                    viz.line([loss_p1.item()], [0.], win='pixel_loss', opts=dict(title='Pixel Loss'))
                    viz.line([loss_p2.item()], [0.], win='shallow_loss', opts=dict(title='Shallow Loss'))
                    viz.line([loss_p3.item()], [0.], win='middle_loss', opts=dict(title='Middle Loss'))
                    viz.line([loss_p4.item()], [0.], win='deep_loss', opts=dict(title='Deep Loss'))

                mesg = "{}\t lam2 {}\t w_ir {}\t lam3 {}\t Count {} \t Epoch {}/{} \t Batch {}/{} \t " \
                       "pixel vi loss: {:.6f} \t fea vi loss: {:.6f} \t " \
                       "fea ir loss: {:.6f}   \t gram ir loss: {:.6f} \n " \
                       "total loss: {:.6f} \n". \
                    format(time.ctime(), lam2_vi, w_ir, lam3_gram, count, e + 1, args.epochs, idx + 1, batch_num,
                           loss_p1, loss_p2, loss_p3, loss_p4, loss_all)

                print(mesg)

                viz.line([loss_all.item()], [viz_index], win='total_loss', update='append')
                viz.line([loss_p1.item()], [viz_index], win='pixel_loss', update='append')
                viz.line([loss_p1.item()], [viz_index], win='shallow_loss', update='append')
                viz.line([loss_p3.item()], [viz_index], win='middle_loss', update='append')
                viz.line([loss_p4.item()], [viz_index], win='deep_loss', update='append')
                viz_index = viz_index + 1

                Loss_list1.append(loss_p1.item())
                Loss_list2.append(loss_p2.item())
                Loss_list3.append(loss_p3.item())
                Loss_list4.append(loss_p4.item())
                Loss_list_all.append(total_loss.item())
                loss_p1 = 0.
                loss_p2 = 0.
                loss_p3 = 0.
                loss_p4 = 0.
                loss_p5 = 0.
                loss_all = 0.

            if count % 1000 == 0:
                temp_loss = str(lam2_vi) + "wir_" + str(w_ir) + "_lam3_gram_" + str(lam3_gram) + "_epoch_" + str(
                    e + 1) + "_batch_" + str(idx + 1) + \
                              str(time.ctime()).replace(' ', '_').replace(':', '_') + ".mat"
                # save 1 loss
                loss_filename_path = "loss_1_lam2_" + temp_loss
                save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
                scio.savemat(save_loss_path, {'loss_1': Loss_list1})
                # save 2 loss
                loss_filename_path = "loss_2_lam2_" + temp_loss
                save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
                scio.savemat(save_loss_path, {'loss_2': Loss_list2})
                # save 3 loss
                loss_filename_path = "loss_3_lam2_" + temp_loss
                save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
                scio.savemat(save_loss_path, {'loss_3': Loss_list3})
                # save 4 loss
                loss_filename_path = "loss_4_lam2_" + temp_loss
                save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
                scio.savemat(save_loss_path, {'loss_4': Loss_list4})
                # save 5 loss
                loss_filename_path = "loss_5_lam2_" + temp_loss
                save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
                scio.savemat(save_loss_path, {'loss_5': Loss_list5})
                # save total loss
                loss_filename_path = "loss_all_lam2_" + temp_loss
                save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
                scio.savemat(save_loss_path, {'loss_all': Loss_list_all})

            if count % 2000 == 0:
                # save model ever 2000 iter.
                model.eval()
                model.cpu()
                save_model_filename = "RepVGGfuse_net_lam2_" + str(lam2_vi) + "_wir_" + str(w_ir) + "_lam3_gram_" + str(lam3_gram) + \
                                      "_epoch_" + str(e + 1) + "_count_" + str(count) + ".model"

                save_model_path = os.path.join(temp_path_model, save_model_filename)
                torch.save(model.state_dict(), save_model_path)
                print('Saving model at ' + save_model_path + '......')
                ##############
                model.train()
                model.cuda()

        # save model
        model.eval()
        model.cpu()
        save_model_filename = "RepVGGfuse_net_lam2_" + str(lam2_vi) + "wir_" + str(w_ir) + "_lam3_gram_" + str(lam3_gram) + \
                              "_epoch_" + str(e + 1) + ".model"

        save_model_path = os.path.join(temp_path_model, save_model_filename)
        torch.save(model.state_dict(), save_model_path)
        ##############
        model.train()
        model.cuda()
        print("\nCheckpoint, trained model saved at: " + save_model_path)
        # 在每个 epoch 结束后计算耗时
        epoch_end_time = time.time()
        epoch_time = epoch_end_time - epoch_start_time
        # 打印当前 epoch 的耗时
        print(f"Epoch [{e + 1}/{args.epochs}] Time: {epoch_time:.2f} seconds")

    # 计算整个训练过程的总耗时
    end_time = time.time()
    total_time = end_time - start_time
    # 打印总耗时
    print(f"Total training time: {total_time:.2f} seconds")

    final_temp = str(lam2_vi) + "_wir_" + str(w_ir) + "_lam3_gram_" + str(lam3_gram)
    # save 1 loss
    loss_filename_path = "final_loss_1_lam2_" + final_temp + ".mat"
    save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
    scio.savemat(save_loss_path, {'loss_1': Loss_list1})
    # save 2 loss
    loss_filename_path = "final_loss_2_lam2_" + final_temp + ".mat"
    save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
    scio.savemat(save_loss_path, {'loss_2': Loss_list2})
    # save 3 loss
    loss_filename_path = "final_loss_3_lam2_" + final_temp + ".mat"
    save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
    scio.savemat(save_loss_path, {'loss_3': Loss_list3})
    # save 4 loss
    loss_filename_path = "final_loss_4_lam2_" + final_temp + ".mat"
    save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
    scio.savemat(save_loss_path, {'loss_4': Loss_list4})
    # save 5 loss
    loss_filename_path = "final_loss_5_lam2_" + final_temp + ".mat"
    save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
    scio.savemat(save_loss_path, {'loss_5': Loss_list5})
    # save total loss
    loss_filename_path = "final_loss_all_lam2_" + final_temp + ".mat"
    save_loss_path = os.path.join(temp_path_loss, loss_filename_path)
    scio.savemat(save_loss_path, {'loss_all': Loss_list_all})
    # save model
    model.eval()
    model.cpu()
    save_model_filename = "final_RepVGGfuse_net_lam2_" + final_temp + ".model"
    save_model_path = os.path.join(temp_path_model, save_model_filename)
    torch.save(model.state_dict(), save_model_path)

    print("\nDone, trained model saved at", save_model_path)


if __name__ == "__main__":
    main()
