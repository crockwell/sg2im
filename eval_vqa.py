import argparse
import h5py
import tqdm
import numpy as np
import pickle
import PIL
import yaml
import torch
from torch import nn
from torch.autograd import Variable
import json
import os
import sys
from tensorboardX import SummaryWriter
from sg2im.data.utils import imagenet_deprocess_batch
from sg2im.model import Sg2ImModel
from sg2im.data.vg_for_vqa import VgSceneGraphDataset, vg_collate_fn
import vqa_pytorch.vqa.datasets as datasets
from vqa_pytorch.vqa.models.att import MutanAtt # vqa model 
#import vqa.models as models
# when i left was running python vqa_pytorch/extract.py --dataset vgenome --dir_data data/vgenome --data_split train
# on flux.

'''
HOW TO RUN:
for all, choose say 100 images with max of 20 questions on each image
1. python generate_imgs.py with our model & their model
2. python extract_chris.py with our model & their model
3. python eval_vqa.py (need to add our model in as well).
* Might want to see which are val / test imgs etc.
'''
    


parser = argparse.ArgumentParser(
    description='Train/Evaluate models',
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--path_opt', default='vqa_pytorch/options/vqa/mutan_att_trainval.yaml', type=str, 
    help='path to a yaml options file')

args = parser.parse_args()
with open(args.path_opt, 'r') as handle:
    options = yaml.load(handle)
    #options = utils.update_values(options, options_yaml)

trainset = datasets.factory_VQA('trainval', opt_vgenome=options['vgenome'], opt=options['vqa'], opt_coco=options['coco'])
LEN_VQA = 334554 # so we can access only visual genome items

# answer questions
def inference(vqa_model, image, question_set):
    '''
    need >1 question in question_set per image.
    no cuda. need our input image to be 
    '''
    answer_set = []
    size_q = len(question_set)
    image = np.tile(image,(size_q,1,1,1))
    #print(type(image))
    #print(np.shape(image))
    #image = np.expand_dims(image.numpy(), axis=0) # to 1,2048,14,14
    input_visual = torch.autograd.Variable(torch.from_numpy(image), requires_grad=False)#.cuda()#Variable(image.cuda(async=True), volatile=True)
    #input_visual = input_visual.permute(0, 3, 1, 2)
    
    question_set = np.array(question_set)
    input_question = torch.autograd.Variable(torch.from_numpy(question_set), requires_grad=False)#.cuda()#Variable(q.cuda(async=True), volatile=True)
    #print(input_visual.size(), input_question.size())
    output = vqa_model(input_visual, input_question)
    #print('o',output)
    _, pred = output.data.cpu().max(1)
    #print('p0',pred)
    pred.squeeze_()
    #print('p1',pred)
    '''
    for q in question_set:
        q = np.expand_dims(q, axis=0)
        input_question = torch.autograd.Variable(torch.from_numpy(q), requires_grad=False).cuda()#Variable(q.cuda(async=True), volatile=True)
        print(input_visual.size(), input_question.size())
        output = vqa_model(input_visual, input_question)
        print(output)
        _, pred = output.data.cpu().max(1)
        print(pred)
        pred.squeeze_()
        print(pred)
        answer_set.append(pred) #output 
    '''
    return pred

def vg_eval(answer_tensors, vqa_gt, vqa_gen_theirs, vqa_gen_mine, answers, i_s):
    '''
    must match answer exactly. However, only use questions with 2 or 1 word answers (relatively easy). 
    May also try 3 word answers
    '''
    gt_corr = {'one': 0, 'two': 0, 'three': 0, 'four': 0}
    gen_corr_theirs = {'one': 0, 'two': 0, 'three': 0, 'four': 0}
    gen_corr_mine = {'one': 0, 'two': 0, 'three': 0, 'four': 0}
    count = {'one': 0, 'two': 0, 'three': 0, 'four': 0}
    img_scores = []
    imgs_count = 0
    for answer_tensor, answer_gt, ans_gen_theirs, ans_gen_mine, answer, i in zip(answer_tensors, vqa_gt, vqa_gen_theirs, vqa_gen_mine, answers, i_s):
        img_score = [0, 0, 0, i, 0]
        imgs_count += 1
        qs_count = 0
        for ans, agt, a, theirs, mine in zip(answer_tensor, answer_gt, answer, ans_gen_theirs, ans_gen_mine):
            word_ct = 'one'
            ct = a.count(' ')
            if ct == 1:
                word_ct = 'two'
            elif ct == 2:
                word_ct = 'three'
            elif ct > 2:
                word_ct = 'four'
            #print(ans, agt.item(), theirs.item(), a) #.item() 
            if ans == agt.item(): #.item() 
                gt_corr[word_ct] += 1
                img_score[0] += 1 
            if ans == theirs.item():
                gen_corr_theirs[word_ct] += 1
                img_score[1] += 1 
            if ans == mine.item():
                gen_corr_mine[word_ct] += 1
                img_score[2] += 1 
            count[word_ct] += 1
            img_score[4] += 1
            qs_count += 1
            if qs_count > 30:
                print('over 30! ', qs_count, i)
        img_scores.append(img_score)
    print('imgs count', imgs_count)
    
    for ct in gt_corr.keys():
        print('accuracy, answers from gt, ', ct, ' word: ', round(gt_corr[ct]/max(count[ct],1),3), 'count', count[ct])
        print('accuracy, answers from generated (theirs), ', ct, ' word: ', round(gen_corr_theirs[ct]/max(count[ct],1),3), 'count', count[ct])
        print('accuracy, answers from generated (mine), ', ct, ' word: ', round(gen_corr_mine[ct]/max(count[ct],1),3), 'count', count[ct])
    total_ct = count['one'] + count['two'] + count['three'] + count['four']
    total_corr = gt_corr['one'] + gt_corr['two'] + gt_corr['three'] + gt_corr['four']
    their_corr = gen_corr_theirs['one'] + gen_corr_theirs['two'] + gen_corr_theirs['three'] + gen_corr_theirs['four']
    my_corr = gen_corr_mine['one'] + gen_corr_mine['two'] + gen_corr_mine['three'] + gen_corr_mine['four']
    print('accuracy, answers from gt, total: ', round(total_corr/max(total_ct,1),3), 'count', total_ct)
    print('accuracy, answers from generated (theirs), total: ', round(their_corr/max(total_ct,1),3), 'count', total_ct)
    print('accuracy, answers from generated (mine), total: ', round(my_corr/max(total_ct,1),3), 'count', total_ct)
    
    img_scores = np.array(img_scores)
    #diffs = img_scores[:,2] - img_scores[:,1] # high is good
    #diffs_gt = img_scores[:,2] - img_scores[:,0] # high is good
    my_scores = img_scores[:,2]
    their_scores = img_scores[:,1]
    gt_scores = img_scores[:,0]
       
    gts_sorted = gt_scores.argsort()[-20:][::-1]
    print('gt best elements', gts_sorted, ', scores: ', img_scores[gts_sorted,:])
    their_sorted = their_scores.argsort()[-20:][::-1]
    print('their best elements', their_sorted, ', scores: ', img_scores[their_sorted,:])
    my_sorted = my_scores.argsort()[-20:][::-1]
    print('our best elements', my_sorted, ', scores: ', img_scores[my_sorted,:])   
        
    #print('median difference in score vs. theirs', np.median(diffs), ', vs. gt: ', np.median(diffs_gt))
    '''
    best_vs_them = diffs.argsort()[-10:][::-1]
    print('our best elements vs. them', best_vs_them, ', scores: ', img_scores[best_vs_them,:])
    best_vs_gt = diffs_gt.argsort()[-10:][::-1]
    print('our best elements vs. gt', best_vs_gt, ', scores: ', img_scores[best_vs_gt,:])
    worst_vs_them = diffs.argsort()[:10]
    print('our worst elements vs. them', worst_vs_them, ', scores: ', img_scores[worst_vs_them,:])
    worst_vs_gt = diffs_gt.argsort()[:10]
    print('our worst elements vs. gt', worst_vs_gt, ', scores: ', img_scores[worst_vs_gt,:])
    '''
    
def generate_img(obj, triple, model):
    '''
    takes scene graph, returns generated img
    '''
    O = objs.size(0)
    obj_to_img = torch.LongTensor(O).fill_(0)
    with torch.no_grad():
        model_out = model(objs, triples, obj_to_img, boxes_gt=model_boxes, masks_gt=model_masks)
    imgs, boxes_pred, masks_pred, predicate_scores = model_out
    #print(np.shape(imgs))
    '''
    with torch.no_grad():
        imgs, boxes_pred, masks_pred, _ = model.forward_json(scene_graph)
    '''
    #imgs = imagenet_deprocess_batch(imgs)
    return imgs

def get_info(num_eval):
    '''
    gets all gt img, question, answer, scene graph
    '''
    VG_DIR = '/scratch/jiadeng_fluxoe/shared/vg'
    vocab_json = os.path.join(VG_DIR, 'vocab.json')
    with open(vocab_json, 'r') as f:
        vocab = json.load(f)
    dset_kwargs = {
        'vocab': vocab,
        'h5_path': os.path.join(VG_DIR, 'test.h5'),
        'image_dir': os.path.join(VG_DIR, 'images'),
        'image_size': (64,64),
        'max_objects': 10,
        'use_orphaned_objects': True,
        'include_relationships': True,
        'normalize_images': False,
    }
    dset = VgSceneGraphDataset(**dset_kwargs)
    with open(os.path.join(VG_DIR, 'question_answers.json')) as data_file:
        data = json.load(data_file)
    
    #with open('qa_from_qid_master.pickle', 'rb') as handle:
    with open('idx_from_qid_master.pickle', 'rb') as handle:
        qa_from_qid = pickle.load(handle)
        
    with open('their_gen_features.pickle', 'rb') as handle:
        their_features = pickle.load(handle)
        
    with open('my_gen_features.pickle', 'rb') as handle:
        my_features = pickle.load(handle)
    
    gotten = 0
    for i in range(190,10000):
        if i % 50 == 0:
            print(i,gotten)
        questions = []
        answers = []
        question_tensors = []
        answer_tensors = []
        gt_img, objs, __, triples, img_id = dset.__getitem__(i)
        gt_img = gt_img.numpy().transpose(1,2,0) #64,64
        feature_tensor = None
        c = 0
        for j in data:
            if j['id'] == img_id:
                added = False
                for k in j['qas']:
                    qid = k['qa_id']
                    try:
                        l = qa_from_qid[qid]#[0]
                    except:
                        continue
                    if k['question'] in questions:
                        continue
                    c += 1
                    questions.append(k['question'])
                    answers.append(k['answer'])
                    item = trainset.__getitem__(l+LEN_VQA)
                    question_tensors.append(item['question'].numpy())
                    answer_tensors.append(item['answer'])
                    if c > 29:
                        break
                if c > 1: # must have at least 2 questions!
                    feature_tensor = item['visual']
        if c > 1:
            their_feats = their_features[i]
            my_feats = my_features[i]
            gotten += 1
            yield gt_img, questions, answers, objs, triples, question_tensors, answer_tensors, feature_tensor, their_feats, my_feats, i
        if gotten >= num_eval-1:
            print('had to go through ',i,' to get ', gotten)
            break

def main():
    '''
    calls fcns to load info, answer questions, evaluate
    '''
    # Load the model, with a bit of care in case there are no GPUs
    print('loading scene gen model...')
    device = torch.device('cuda:0')
    map_location = 'cpu' if device == torch.device('cpu') else None
    checkpoint_theirs = torch.load('sg2im-models/vg64.pt', map_location=map_location)
    their_model = Sg2ImModel(**checkpoint_theirs['model_kwargs'])
    their_model.load_state_dict(checkpoint_theirs['model_state'])
    their_model.eval()
    their_model.to(device)
    
    checkpoint_mine = torch.load('vg_only.pt', map_location=map_location)
    my_model = Sg2ImModel(**checkpoint_mine['model_kwargs'])
    my_model.load_state_dict(checkpoint_mine['model_state'])
    my_model.eval()
    my_model.to(device)
    
    # load vqa model 
    
    gt_imgs = []
    #gen_imgs = []
    questions = []
    answers = []
    question_tensors = []
    answer_tensors = []
    vqa_gt = []
    vqa_gen_theirs = []
    vqa_gen_mine = []
    objs = []
    triples = []
    feature_tensors = []
    their_feat_tensors = []
    my_feat_tensors = []
    i_s = []

    print('getting q, a, images...')
    for gt_img, question_set, answer_set, obj, triple, question_tensor_set, answer_tensor_set, feature_tensor, their_feats, my_feats, i in get_info(num_eval=500): #1000
        gt_imgs.append(gt_img)
        questions.append(question_set)
        answers.append(answer_set)
        objs.append(obj)
        triples.append(triple)
        question_tensors.append(question_tensor_set)
        answer_tensors.append(answer_tensor_set)
        feature_tensors.append(feature_tensor)
        their_feat_tensors.append(their_feats)
        my_feat_tensors.append(my_feats)
        i_s.append(i)
    '''    
    vocab_answers = []
    vocab_words = []
    for i in range(len(questions)):
        for j in range(len(questions[i])):
            vocab_answers.append(answers[i][j])
            words = questions[i][j].split()
            for word in words:
                vocab_words.append(word)
    print(len(vocab_answers), len(vocab_words))        
    vqa_model = MutanAtt(options['model'], vocab_words, vocab_answers)#trainset.vocab_words(), trainset.vocab_answers())
    '''
    print('loading vqa model...')
    vqa_model = MutanAtt(options['model'], trainset.vocab_words(), trainset.vocab_answers())
    
    path_ckpt_model = 'vqa_pytorch/vqa/mutan_att_trainval/ckpt_model.pth.tar'
    model_state = torch.load(path_ckpt_model)
    vqa_model.load_state_dict(model_state)
    vqa_model.eval()
        
    for i in range(len(gt_imgs)):
        gt_img = gt_imgs[i]
        question_set = questions[i]
        question_set_tensors = question_tensors[i]
        obj = objs[i]
        feature_tensor = feature_tensors[i]
        triple = triples[i]
        their_feats = their_feat_tensors[i]
        my_feats = my_feat_tensors[i]
        #img_paths.append(img_path)
        
        # answer, gt
        vqa_answer_from_gt = inference(vqa_model, feature_tensor, question_set_tensors)
        #print(vqa_answer_from_gt)
        vqa_gt.append(vqa_answer_from_gt)
        
        vqa_theirs = inference(vqa_model, their_feats, question_set_tensors)
        vqa_gen_theirs.append(vqa_theirs)
        
        vqa_mine = inference(vqa_model, my_feats, question_set_tensors)
        vqa_gen_mine.append(vqa_mine)

    vg_eval(answer_tensors, vqa_gt, vqa_gen_theirs, vqa_gen_mine, answers, i_s)
    
if __name__ == '__main__':
    main()    
    