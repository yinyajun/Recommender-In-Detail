#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2018/11/27 17:46

from __future__ import absolute_import
from __future__ import print_function

import time
import os
import tensorflow as tf
import numpy as np
import utils as ut
from model import CausalProd2Vec2i
from tensorflow.contrib.tensorboard.plugins import projector
from evaluation import *

# Hyper-Parameters
flags = tf.app.flags
FLAGS = flags.FLAGS
flags.DEFINE_string('data_set', 'user_prod_dict.skew.', 'Dataset string.')
flags.DEFINE_integer('num_products', 9724, 'How many products in the dataset.')
flags.DEFINE_integer('num_users', 610, 'How many users in the dataset.')
flags.DEFINE_string('adapt_stat', 'adapt_2i', 'Adapt String.')
flags.DEFINE_string('model_name', 'cp2v', 'Name of the model for saving.')
flags.DEFINE_string('logging_dir', './tmp/tensorboard', 'Name of the model for saving.')
flags.DEFINE_float('learning_rate', 0.8, 'Initial learning rate.')
flags.DEFINE_float('l2_pen', 0.5, 'L2 learning rate penalty.')
flags.DEFINE_integer('num_epochs', 10, 'Number of epochs to train.')
flags.DEFINE_integer('batch_size', 512, 'How big is a batch of training.')
flags.DEFINE_integer('num_steps', 40, 'Number of steps after which to test.')
flags.DEFINE_bool('early_stopping_enabled', False, 'Enable early stopping.')
flags.DEFINE_bool('plot_gradients', False, 'Plot the gradients in Tensorboard.')
flags.DEFINE_integer('early_stopping', 200, 'Tolerance for early stopping (# of epochs).')
flags.DEFINE_integer('seed', 123, 'Set for reproducibility.')
flags.DEFINE_integer('embedding_size', 10, 'Size of each embedding vector.')
flags.DEFINE_float('cf_pen', 0.5, 'Imbalance loss.')
flags.DEFINE_string('cf_distance', 'l1', 'Use L1 or L2 for the loss .')

train_data_set_location = "data/" + FLAGS.data_set + "train." + FLAGS.adapt_stat + ".csv"  # Location of train dataset
test_data_set_location = "data/" + FLAGS.data_set + "test." + FLAGS.adapt_stat + ".csv"  # Location of the test dataset
validation_data_set_location = "data/" + FLAGS.data_set + "valid." + FLAGS.adapt_stat + ".csv"  # Location of the validation dataset

model_name = FLAGS.model_name + ".ckpt"
cost_val = []
# double
FLAGS.num_products = FLAGS.num_products * 2

# Create graph object
graph = tf.Graph()
with graph.as_default():
    with tf.device('/cpu:0'):
        tf.set_random_seed(FLAGS.seed)

        # Load the model
        model = CausalProd2Vec2i(FLAGS)

        # Get data batch from queue
        next_batch = ut.load_train_dataset(train_data_set_location, FLAGS.batch_size, FLAGS.num_epochs)
        test_user_batch, test_product_batch, test_label_batch, test_cr = ut.load_test_dataset(test_data_set_location)
        val_user_batch, val_product_batch, val_label_batch, val_cr = ut.load_test_dataset(validation_data_set_location)

        # create the empirical CR test logits
        test_logits = np.empty(len(test_label_batch))
        test_logits.fill(test_cr)

# Launch the Session
with tf.Session(graph=graph, config=tf.ConfigProto(allow_soft_placement=True, log_device_placement=False)) as sess:
    # initialise all the TF variables
    init_op = tf.global_variables_initializer()
    sess.run(init_op)

    # Plot the gradients if required.
    if FLAGS.plot_gradients:
        # Create summaries to visualize weights
        for var in tf.trainable_variables():
            tf.summary.histogram(var.name, var)
        # Summarize all gradients
        for grad, var in model.grads:
            tf.summary.histogram(var.name + '/gradient', grad)

    # Setup tensorboard
    time_tb = str(time.time())
    train_writer = tf.summary.FileWriter('./tmp/tensorboard' + '/train' + time_tb, sess.graph)
    test_writer = tf.summary.FileWriter('./tmp/tensorboard' + '/test' + time_tb, sess.graph)
    merged = tf.summary.merge_all()

    # Embeddings viz (Possible to add labels for embeddings later)
    saver = tf.train.Saver()
    config = projector.ProjectorConfig()
    embedding = config.embeddings.add()
    embedding.tensor_name = model.product_embeddings.name
    projector.visualize_embeddings(train_writer, config)

    # Variables used in the training loop
    t = time.time()
    step = 0
    average_loss = 0
    average_mse_loss = 0
    average_log_loss = 0

    # Start the training loop-------------------------------------------------------------------------
    print("Starting Training On Causal Prod2Vec")
    print("Num Epochs = ", FLAGS.num_epochs)
    print("Learning Rate = ", FLAGS.learning_rate)
    print("L2 Reg = ", FLAGS.l2_pen)

    try:
        while True:
            # Run the TRAIN for this step batch ---------------------------------------------------------------------
            with tf.device('/cpu:0'):
                # Construct the feed_dict
                user_batch, product_batch, label_batch = sess.run(next_batch)
                # Treatment is the small set of samples from St, Control is the larger set of samples from Sc
                reg_ids = ut.compute_treatment_id(product_batch, (FLAGS.num_products / 2))
                feed_dict = {model.user_list: user_batch,
                             model.prod_list: product_batch,
                             model.treatment_prod_list: reg_ids,
                             model.label_list: label_batch}

                # Run the graph
                _, sum_str, loss_val, mse_loss_val, log_loss_val = sess.run(
                    [model.apply_grads, merged, model.loss, model.mse_loss, model.log_loss], feed_dict=feed_dict)

            step += 1
            average_loss += loss_val
            average_mse_loss += mse_loss_val
            average_log_loss += log_loss_val

            # Every num_steps print average loss
            if step % FLAGS.num_steps == 0:
                if step > FLAGS.num_steps:
                    # The average loss is an estimate of the loss over the last set batches.
                    average_loss /= FLAGS.num_steps
                    average_mse_loss /= FLAGS.num_steps
                    average_log_loss /= FLAGS.num_steps
                print("Average Training Loss on S_c (FULL, MSE, NLL) at step ", step, ": ", average_loss, ": ",
                      average_mse_loss, ": ", average_log_loss, "Time taken (S) = " + str(round(time.time() - t, 1)))

                average_loss = 0
                t = time.time()  # reset the time
                train_writer.add_summary(sum_str, step)  # Write the summary

                # Run the VALIDATION for this step batch -------------------------------------
                val_product_batch = np.asarray(val_product_batch, dtype=np.float32)
                val_reg_ids = ut.compute_treatment_id(val_product_batch, (
                    FLAGS.num_products / 2))

                feed_dict_test = {model.user_list: val_user_batch,
                                  model.prod_list: val_product_batch,
                                  model.treatment_prod_list: val_reg_ids,
                                  model.label_list: val_label_batch}

                # Run TEST distribution validation
                sum_str, loss_val, mse_loss_val, log_loss_val = sess.run(
                    [merged, model.loss, model.mse_loss, model.log_loss], feed_dict=feed_dict_test)
                cost_val.append(loss_val)
                print("Validation loss (FULL, MSE, NLL) at step ", step, ": ", loss_val, ": ", mse_loss_val, ": ",
                      log_loss_val)

                print("######################################################################################")

                test_writer.add_summary(sum_str, step)  # Write the summary

                # If condition for the early stopping condition
                if FLAGS.early_stopping_enabled and step > FLAGS.early_stopping and cost_val[-1] > np.mean(
                        cost_val[-(FLAGS.early_stopping + 1):-1]):
                    print("Early stopping...")
                    saver.save(sess, os.path.join(FLAGS.logging_dir, model_name), model.global_step)  # Save model
                    break

    except tf.errors.OutOfRangeError:
        print("Reached the number of epochs")

    finally:
        with tf.device('/cpu:0'):
            saver.save(sess, os.path.join(FLAGS.logging_dir, model_name), model.global_step)  # Save model

    train_writer.close()
    print("Training Complete")

    # Run the test set for the trained model -----------------------------------------------------------------------
    print("Running Test Set")
    feed_dict = {model.user_list: test_user_batch,
                 model.prod_list: test_product_batch,
                 model.treatment_prod_list: test_product_batch,
                 model.label_list: test_label_batch}
    loss_val, mse_loss_val, log_loss_val = sess.run([model.loss, model.mse_loss, model.log_loss], feed_dict=feed_dict)
    print("Test loss (CE, MSE, NLL) = ", loss_val, ": ", mse_loss_val, ": ", log_loss_val)

    # Run the bootstrap for this model -----------------------------------------------------------------------------
    print("Begin Bootstrap process...")
    print(">>> Running BootStrap On The Treatment Representations")
    ut.compute_bootstraps_2i(sess, model, test_user_batch, test_product_batch, test_label_batch, test_logits,
                             model.ap_mse_loss, model.ap_log_loss)

    print(">>> Running BootStrap On The Control Representations...")
    test_product_batch = [int(x) + (FLAGS.num_products / 2) for x in test_product_batch]
    ut.compute_bootstraps_2i(sess, model, test_user_batch, test_product_batch, test_label_batch, test_logits,
                             model.ap_mse_loss, model.ap_log_loss)

    # evaluate
    evaluation_model(sess, model)

