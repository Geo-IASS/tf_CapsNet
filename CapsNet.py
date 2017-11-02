import tensorflow as tf
from ops import *

class CapsNet(object):
    def __init__(self,routing_iterations = 3,batch_size=128,is_multi_mnist=False):
        self.iterations = routing_iterations
        self.batch_size = batch_size
        self.is_multi_mnist = float(is_multi_mnist)

        self.x = tf.placeholder(tf.float32, [None, 28, 28, 1])
        self.h_sample = tf.placeholder(tf.float32, [None, 10, 16])
        self.y_sample = tf.placeholder(tf.float32, [None, 10])
        self.y = tf.placeholder(tf.float32, [None, 10, 3])

        y0,y1,y2 = self.y[:,:,0:1],self.y[:,:,1:2],self.y[:,:,2:3]

        enable_mask = self.is_multi_mnist * (tf.reduce_sum(y0, axis=[1,2]) - 1.0) \
                      + (1.0 - self.is_multi_mnist) * tf.ones_like(y0[:,0,0])

        v_digit,c = self.get_CapsNet()
        length_v = tf.reduce_sum(v_digit ** 2.0, axis=-1) ** 0.5  # length_v with shape [batch_size,10]

        if is_multi_mnist:
            x_rec1 = self.get_mlp_decoder(v_digit * y1)
            x_rec2 = self.get_mlp_decoder(v_digit * y2,reuse=True)
            self.x_rec = self.get_mlp_decoder(v_digit * y0,reuse=True)
            self.x_recs = [x_rec1,x_rec2]
        else:
            self.x_rec = self.get_mlp_decoder(v_digit * y0)
            self.x_recs = [self.x_rec]
        self.x_sample = self.get_mlp_decoder(self.h_sample * self.y_sample[:, :, None], reuse=True)

        self.loss_cls = tf.reduce_sum(y0[:,:,0] * tf.maximum(0.0, 0.9 - length_v) ** 2.0
                                      + 0.5 * (1.0 - y0[:,:,0]) * tf.maximum(0.0, length_v - 0.1) ** 2.0,axis=-1)
        self.loss_rec = tf.reduce_sum((self.x_rec-self.x)**2.0, axis=[1, 2, 3])
        self.loss_cls = tf.reduce_sum(self.loss_cls*enable_mask)/tf.reduce_sum(enable_mask)
        self.loss_rec = tf.reduce_sum(self.loss_rec*enable_mask)/tf.reduce_sum(enable_mask)
        self.loss = self.loss_cls + 0.0005*self.loss_rec

        self.train = tf.train.AdamOptimizer().minimize(self.loss)

        if is_multi_mnist:
            self.accuracy = tf.reduce_mean(tf.cast(tf.nn.in_top_k(length_v,tf.argmax(y1[:,:,0], 1),k=2),tf.float32))+\
                            tf.reduce_mean(tf.cast(tf.nn.in_top_k(length_v,tf.argmax(y2[:,:,0], 1),k=2),tf.float32))
            self.accuracy /= 2.0
            #this may be different from the paper
        else:
            correct_prediction = tf.equal(tf.argmax(y0[:,:,0], 1), tf.argmax(length_v, 1))
            self.accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))

    def get_CapsNet(self,reuse = False):
        with tf.variable_scope('CapsNet',reuse=reuse):
            wconv1 = tf.get_variable('wconv1',[9,9,1,256],initializer=tf.truncated_normal_initializer(stddev=0.02))
            bconv1 = tf.get_variable('bconv1', [256], initializer=tf.truncated_normal_initializer(stddev=0.02))
            wconv2 = tf.get_variable('wconv2',[9,9,256,8*32],initializer=tf.truncated_normal_initializer(stddev=0.02))
            bconv2 = tf.get_variable('bconv2', [8*32], initializer=tf.truncated_normal_initializer(stddev=0.02))
            #[batch,i_row,i_column,i_channel,u,j,v]
            #[0    ,1    ,2       ,3        ,4,5,6]
            wcap = tf.get_variable('wcap',[1,6,6,32,8,10,16],initializer=tf.truncated_normal_initializer(stddev=0.02))
            b = tf.get_variable('coupling_coefficient_logits',[1,6,6,32,1,10,1],initializer=tf.constant_initializer(0.0))

        c = tf.stop_gradient(tf.nn.softmax(b, dim=5))

        conv1 = relu(conv1x1(self.x,wconv1)+bconv1)
        s_primary = conv2x2(conv1,wconv2)+bconv2 #with shape [batch_size,6,6,8*32]
        s_primary = tf.reshape(s_primary,[-1,6,6,32,8,1,1])
        v_primary = self.squash(s_primary,axis=4)

        u = v_primary
        u_ = tf.reduce_sum(u*wcap,axis=[4],keep_dims=True)
        s = tf.reduce_sum(u_*c,axis=[1,2,3],keep_dims=True)
        v = self.squash(s,axis=-1)

        #u_ with shape [batch_size,6,6,32,1,10,16]
        #v  with shape [batch_size,1,1, 1,1,10,16]

        for i in range(self.iterations-1):
            b += tf.reduce_sum(u_*v,axis=-1,keep_dims=True)
            c =  tf.nn.softmax(b, dim=5)
            s = tf.reduce_sum(u_ * c, axis=[1, 2, 3], keep_dims=True)
            v = self.squash(s,axis=-1)

        v_digit = tf.squeeze(v) #v_digit with shape [batch_size,10,16]

        return v_digit,c

    def get_mlp_decoder(self,h,num_h=[10*16,512,1024,784],reuse=False):
        h = tf.reshape(h,[-1,10*16])
        with tf.variable_scope('decoder',reuse=reuse):
            weights = []
            for i in range(len(num_h)-1):
                w = tf.get_variable('wfc%d'%i,[num_h[i],num_h[i+1]],initializer=tf.truncated_normal_initializer(stddev=0.02))
                b = tf.get_variable('bfc%d'%i,[num_h[i+1]],initializer=tf.truncated_normal_initializer(stddev=0.02))
                weights.append((w,b))
                if i<len(num_h)-2:
                    h = relu(fullyconnect(h,w,b))
                else:
                    h = tf.nn.sigmoid(fullyconnect(h,w,b))
        x_rec = tf.reshape(h,[-1,28,28,1])
        return x_rec#,weights

    def squash(self,s, axis=-1):
        length_s = tf.reduce_sum(s ** 2.0, axis=axis, keep_dims=True) ** 0.5
        v = s * length_s / (1.0 + length_s ** 2.0)
        return v

