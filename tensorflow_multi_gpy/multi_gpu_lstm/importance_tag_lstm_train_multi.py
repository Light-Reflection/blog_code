#coding=utf-8

# ==============================================================================
# Author: shixiang08abc@gmail.com
# Copyright 2017 Sogou Inc. All Rights Reserved.
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
import numpy as np
import os
import re
import sys
import time
import WordEmbedding
import datetime

from tensorflow.python.ops import rnn
from tensorflow.python.ops import rnn_cell
from tensorflow.python.ops import init_ops
from tensorflow.core.protobuf import saver_pb2

class Config(object):
  def __init__(self):
    self.batch_size = 40000
    self.max_length = 30
    self.learning_rate = 0.0001
    self.momentum = 0.9
    self.max_epoch = 10
    self.target_delay = 5
    self.vocab_size = 450000
    self.embedding_dim = 100
    #self.tag_size = 32
   # self.tag_dim = 10
    self.cell_size = 256
    self.target_size = 1
    self.project_size = 128

    ###zsw  2018.08.15
    self.saveModelEvery = 10000

    self.modelDir = "./models/"
    self.modelName = "lstmp"
    self.trainFileDir = "./src/"
    self.wordTablePath = './data/word_table.dic'

    self.gpu_list = [0, 1, 2, 3]  ####需要是按照从到达的顺序
    self.CUDA_VISIBLE_DEVICES = ",".join([str(x) for x in self.gpu_list])





def getFileNames(mydir):
  filenames = []
  for filename in os.listdir(os.path.dirname(mydir)):
    if re.match('^shuffle_data$',filename):
      filenames.append(mydir+filename)

  return filenames

class createLstmModel(object):
  def __init__(self,config):
     with tf.device('/cpu:0'):
        #self.tensor_table = {}
        self.batch_size = config.batch_size
        self.num_steps = config.max_length
        self.learning_rate = config.learning_rate
        self.momentum = config.momentum
        self.target_delay = config.target_delay
        self.vocab_size = config.vocab_size
        self.embedding_dim = config.embedding_dim
        self.gpu_list = config.gpu_list
        #self.embedding = embedding

        # self.tag_size = config.tag_size
        # self.tag_dim = config.tag_dim

        self.cell_size = config.cell_size
        self.target_size = config.target_size
        self.project_size = config.project_size

        print('build model...')
        print('build model on gpu tower...')
        self.models = []
        optimizer = tf.contrib.opt.LazyAdamOptimizer(self.learning_rate)
        emb = np.random.rand(self.vocab_size+1,self.embedding_dim)
  #      with tf.variable_scope(tf.get_variable_scope()):
   #       for gpu_id in self.gpu_list:
    #        with tf.device('/gpu:%d' % gpu_id):
     #         print ('tower:%d...'% gpu_id)
      #        with tf.name_scope('tower_%d' % gpu_id) :
        for gpu_id in self.gpu_list:
           with tf.device('/gpu:%d' % gpu_id):
              print ('tower:%d...'% gpu_id)
              with tf.name_scope('tower_%d' % gpu_id) :
                with tf.variable_scope('gpu_v', reuse=gpu_id > self.gpu_list[0]):
                    id2embedding = tf.get_variable("embedding", 
						   shape = [self.vocab_size+1,self.embedding_dim],
						   initializer=tf.constant_initializer(emb),#[self.vocab_size + 1, self.embedding_dim],
                                                   dtype=tf.float32,
                                                   trainable=False)
                    _input_data = tf.placeholder(tf.int32, [self.batch_size, self.num_steps+self.target_delay])
                    _targets = tf.placeholder(tf.float32, [self.batch_size, self.num_steps+self.target_delay])
                    _lengths = tf.placeholder(tf.int32, [self.batch_size])
                    _frame_weight = tf.placeholder(tf.float32, [self.batch_size, self.num_steps+self.target_delay])
                    pred,loss,true_loss= self.build_model(_input_data,_targets,_lengths,_frame_weight,id2embedding )
		    tf.get_variable_scope().reuse_variables()
                    grads = optimizer.compute_gradients(loss)
                     # self._train_op = optimizer.minimize(loss)
                    self.models.append((_input_data,_targets,_lengths,_frame_weight,pred,loss,true_loss,grads))
        print('build model on gpu tower done.')
        #var_list = tf.global_variables()
       # self.print_variable(var_list)

        print('reduce model on cpu...')
        tower_input_data,tower_targets, tower_lengths, tower_frame_weight, tower_pred, tower_loss, tower_true_loss,tower_grads = zip(*(self.models))
        self.aver_loss_op = tf.reduce_mean(tower_loss)
        self.aver_true_loss = tf.reduce_mean(tower_true_loss)
        self.apply_gradient_op = optimizer.apply_gradients(self.average_gradients(tower_grads))

  def build_model(self,_input_data,_targets,_lengths,_frame_weight,id2embedding):
      ### num_proj=  projection layer
      ### use_peepholes     C_t
      ###forget_bias   add to forget gate bias
      lstm_cell = tf.contrib.rnn.LSTMCell(self.cell_size, num_proj=self.project_size, use_peepholes=True, forget_bias=0.0)


      word_embedding = tf.nn.embedding_lookup(id2embedding, _input_data)
      #tag_embedding = tf.nn.embedding_lookup(embedding.id2tagembedding, self._input_tag)
      #concat_embedding = tf.concat([word_embedding, tag_embedding], 2)
      lengths = tf.reshape(_lengths, [self.batch_size])
      targets = tf.reshape(tf.concat(_targets, 0), [self.batch_size*(self.num_steps+self.target_delay)])
      frame_weight = tf.reshape(tf.concat(_frame_weight, 0), [self.batch_size*(self.num_steps+self.target_delay)])
      #'outputs' is a tensor of shape [batch_size, max_time, cell_state_size]
      #self.output, _ = tf.nn.dynamic_rnn(lstm_cell, concat_embedding, sequence_length=self.lengths, dtype=tf.float32)
      output, _ = tf.nn.dynamic_rnn(lstm_cell, word_embedding, sequence_length=lengths, dtype=tf.float32)

      softmax_fw_w = tf.get_variable("softmax_fw_w", [self.project_size, self.target_size],trainable=True)
      ### trainable == True   ????????
      ### softmax_fw_b's   length

      softmax_fw_b = tf.get_variable("softmax_fw_b", [self.target_size], initializer=init_ops.constant_initializer(0.0) ,trainable=True)
      logits_fw = tf.matmul(tf.reshape(output, [-1, self.project_size]), softmax_fw_w) + softmax_fw_b
      #self.logits_tar = tf.reshape(self.logits_fw, [self.batch_size*(self.num_steps+self.target_delay)])
      logits = tf.sigmoid(tf.reshape(logits_fw, [self.batch_size*(self.num_steps+self.target_delay)]))
      mse_lose = (logits - targets) ** 2 * frame_weight / 2
      # self.lose = tf.nn.sigmoid_cross_entropy_with_logits(labels=self.targets, logits=self.logits_tar)
      # self._lose = tf.reduce_sum(self.lose)
      true_lose = tf.reduce_sum(mse_lose) / tf.reduce_sum(frame_weight)

      regularization_cost = 0.001 * tf.reduce_sum([tf.nn.l2_loss(softmax_fw_w)])
      # tvars = tf.trainable_variables()
      # self.grads = tf.gradients(self.mse_lose,tvars)
      # optimizer = tf.train.MomentumOptimizer(self.learning_rate,self.momentum)
      # self._train_op = optimizer.apply_gradients(zip(self.grads,tvars))
      loss = mse_lose + regularization_cost


      return logits,loss,true_lose

  def print_variable(self,var_list):
    print("---------- Model Variabels -----------")
    cnt = 0
    for var in var_list:
      cnt += 1
      try:
        var_shape = var.get_shape()
      except:
        var_shape = var.dense_shape
      str_line = str(cnt) + '. ' + str(var.name) + '\t' + str(var.device)  + '\t' + str(var_shape) + '\t' + str(var.dtype.base_dtype)
      print(str_line)
    print('------------------------')

  def average_gradients(self,tower_grads):
      average_grads = []
      for grad_and_vars in zip(*tower_grads):
          # Note that each grad_and_vars looks like the following:
          #   ((grad0_gpu0, var0_gpu0), ... , (grad0_gpuN, var0_gpuN))
          grads = [g for g, _ in grad_and_vars]
          # Average over the 'tower' dimension.
          grad = tf.stack(grads, 0)
          grad = tf.reduce_mean(grad, 0)

          # Keep in mind that the Variables are redundant because they are shared
          # across towers. So .. we will just return the first tower's pointer to
          # the Variable.
          v = grad_and_vars[0][1]
          grad_and_var = (grad, v)
          average_grads.append(grad_and_var)
      return average_grads

def feed_all_gpu(inp_dict, lstmModel, data):
    for i in range(len(lstmModel.models)):
        _input_data, _targets, _lengths, _frame_weight, _, _, _, _= lstmModel.models[i]
        data_list, target_list, length_list, frame_weight = data[i]
        inp_dict[_input_data] = data_list
        inp_dict[_targets] = target_list
        inp_dict[_lengths] = length_list
        inp_dict[_frame_weight] = frame_weight
    return inp_dict

def run_epoch(sess, models, w2v, train_file, epoch_id,steps,saveModelEvery,modelPath,modelName,num_gpu):
  print("file=%s, epoch=%d begins:" % (train_file,epoch_id))
  start_time = time.time()

  costsOfEpoch = 0.0
  stepsOfEpoch = 0

  costsOfBatch = 0.0
  stepsOfBatch = 0

  discardLines = 0
  tmpstep = 0

  fin = open(train_file,"r")
  saver = tf.train.Saver(write_version=saver_pb2.SaverDef.V1)
  sstart_time = time.time()
  while True:
      #success,data_list,tag_list,target_list,length_list,frame_weight = w2v.getImportanceBatchData(fin)
      tower_data =[]
      successMark = True
      for i in range(num_gpu):
        success, data_list, target_list, length_list, frame_weight,discardLines = w2v.getImportanceBatchData(fin,discardLines)
        tower_data.append((data_list, target_list, length_list, frame_weight))
        if not success:
            successMark = False
            break
      if not successMark:
            break
      inp_dict = {}
      inp_dict = feed_all_gpu(inp_dict, models,tower_data)
      cost , _ ,_= sess.run([models.aver_true_loss,models.aver_loss_op,models.apply_gradient_op],inp_dict )
      costsOfEpoch += cost
      costsOfBatch += cost
      steps += 1
      stepsOfEpoch += 1
      stepsOfBatch += 1
      tmpstep+=1

      if tmpstep%10==0:
          nowTime=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
          print("%s : avg cost after %5d batches: cur_loss=%.6f, avg_loss=%.6f, %5.2f seconds elapsed ..." % (nowTime, steps, costsOfBatch/stepsOfBatch, (costsOfEpoch/stepsOfEpoch), (time.time()-start_time)))
          costsOfBatch = 0.0
          stepsOfBatch = 0
          sys.stdout.flush()
      #if steps % saveModelEvery == 0:
      #    saver.save(sess, modelPath+ modelName + '%03d' % epoch_id, write_meta_graph=False)
  stop_time = time.time()
  elapsed_time = stop_time - sstart_time
  print('Cost time: {} sec.'.format( elapsed_time))
  fin.close()
  print ("discardLines counter : " + str(discardLines))
  return  steps,elapsed_time

  #saver_emb = tf.train.Saver({'embedding':w2v.id2embedding}, write_version=saver_pb2.SaverDef.V1)
  #saver_emb.save(session, 'models/lstmp_outter_embedding_refine_'+'%03d'%epoch_id, write_meta_graph=False)

def print_variable(var_list):
    print("---------- Model Variabels -----------")
    cnt = 0
    for var in var_list:
      cnt += 1
      try:
        var_shape = var.get_shape()
      except:
        var_shape = var.dense_shape
      str_line = str(cnt) + '. ' + str(var.name) + '\t' + str(var.device)  + '\t' + str(var_shape) + '\t' + str(var.dtype.base_dtype)
      print(str_line)
    print('------------------------')

def main(unused_args):
 with tf.device('/cpu:0'):
  myconfig = Config()
  w2v = WordEmbedding.Word2Vec(myconfig)
  start_time = time.time()
  w2v.loadWordFile(myconfig.wordTablePath)
  end_time = time.time()
  sys.stderr.write(' %.2f'%(end_time-start_time) + ' seconds escaped...\n')
  trainnames = getFileNames(myconfig.trainFileDir)

  #按照文件顺序读取数据
  trainnames.sort()
  print("train file list:\n")
  for i in trainnames:
    print(i)

  os.environ["CUDA_VISIBLE_DEVICES"] = myconfig.CUDA_VISIBLE_DEVICES
  configproto = tf.ConfigProto()
  #configproto.gpu_options.allow_growth = True
  configproto.allow_soft_placement = True
  configproto.log_device_placement=True
  timeList = []
  with tf.Session(config=configproto) as sess:
        filenum = len(trainnames)
        lstm_models = createLstmModel(myconfig)
        init = tf.global_variables_initializer()
        sess.run(init)

        var_list = tf.global_variables()
        print_variable(var_list)
        #loader = tf.train.Saver()
        #loader.restore(sess, "models/lstmp_imp_refine_100")
        steps = 0

        for i in range(myconfig.max_epoch):
            steps ,elapsed_time= run_epoch(sess, lstm_models, w2v, trainnames[i%filenum], i+1,steps,myconfig.saveModelEvery,myconfig.modelDir,myconfig.modelName,len(myconfig.gpu_list))
            timeList.append(elapsed_time)
  all = 0
  for epoch,t in enumerate(timeList):
      print ("epoch:{}   used time:{}".format(epoch,t))
      all+=t
  print("run {} epoch use time:{}".format(len(timeList),all))
if __name__=="__main__":
  tf.app.run()
