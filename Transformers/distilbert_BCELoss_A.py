# Importing stock ml libraries
import warnings
warnings.simplefilter('ignore')
import numpy as np
import pandas as pd
from tqdm import tqdm
from sklearn import metrics
import transformers
import torch
from torch.utils.data import Dataset, DataLoader, RandomSampler, SequentialSampler
from transformers import DistilBertTokenizer, DistilBertModel
import logging
import json
logging.basicConfig(level=logging.ERROR)
import os
import pickle

# Source Data
dataset = "distilbert_R21578"   #[ 'R21578', 'RCV1-V2', 'Econbiz', 'Amazon-531', 'DBPedia-298','NYT AC','GoEmotions']
num_labels = 90                     #[90,101,5658,512,298,166,28]
epochs = 15                      #[15,15,15,15,5,15,5]
train_list = json.load(open("./multi_label_data/reuters/train_data.json", )) #change dataset name [ 'reuters', 'rcv1-v2', 'econbiz', 'amazon', 'dbpedia','nyt','goemotions']
train_data = np.array(list(map(lambda x: (list(x.values())[:2]), train_list)),dtype=object)
train_labels= np.array(list(map(lambda x: list(x.values())[2], train_list)),dtype=object)

test_list = json.load(open("./multi_label_data/reuters/test_data.json", )) #change dataset name
train_list = train_list + test_list
test_data = np.array(list(map(lambda x: list(x.values())[:2], test_list)),dtype=object)
test_labels = np.array(list(map(lambda x: list(x.values())[2], test_list)),dtype=object)

class_freq=pickle.load(open(os.path.join("./multi_label_data/reuters/class_freq.rand123"),'rb'))
train_num=pickle.load(open(os.path.join("./multi_label_data/reuters/train_num.rand123"),'rb'))
print(class_freq)
print(train_num)


# Preprocess Labels
from sklearn.preprocessing import MultiLabelBinarizer
label_encoder = MultiLabelBinarizer()
label_encoder.fit(train_labels)
train_labels_enc = label_encoder.transform(train_labels)
test_labels_enc = label_encoder.transform(test_labels)

# Create DataFrames
train_df = pd.DataFrame()
train_df['text'] = train_data[:,1]
train_df['labels'] = train_labels_enc.tolist()

test_df = pd.DataFrame()
test_df['text'] = test_data[:,1]
test_df['labels'] = test_labels_enc.tolist()

print("Number of train texts ",len(train_df['text']))
print("Number of train labels ",len(train_df['labels']))
print("Number of test texts ",len(test_df['text']))
print("Number of test labels ",len(test_df['labels']))

# Setting up the device for GPU usage
from torch import cuda
device = 'cuda' if cuda.is_available() else 'cpu'

# Sections of config

# Defining some key variables that will be used later on in the training
MAX_LEN = 512
TRAIN_BATCH_SIZE = 4
VALID_BATCH_SIZE = 4
EPOCHS = epochs
LEARNING_RATE = 1e-05
tokenizer = DistilBertTokenizer.from_pretrained('distilbert-base-uncased', truncation=True, do_lower_case=True)

# Define CustomDataset
class MultiLabelDataset(Dataset):

    def __init__(self, dataframe, tokenizer, max_len):
        self.tokenizer = tokenizer
        self.data = dataframe
        self.text = dataframe.text
        self.targets = self.data.labels
        self.max_len = max_len

    def __len__(self):
      return len(self.text)

    def __getitem__(self, index):
        text = str(self.text[index])
        text = " ".join(text.split())

        inputs = self.tokenizer.encode_plus(
            text,
            None,
            add_special_tokens=True,
            max_length=self.max_len,
            pad_to_max_length=True,
            return_token_type_ids=True
        )
        ids = inputs['input_ids']
        mask = inputs['attention_mask']
       


        return {
            'ids': torch.tensor(ids, dtype=torch.long),
            'mask': torch.tensor(mask, dtype=torch.long),
            'targets': torch.tensor(self.targets[index], dtype=torch.float)
        }

train_df

# Creating the dataset and dataloader for the neural network
# Train-Val-Test Split
MAX_LEN = 512
train_size = 0.8
train_dataset = train_df.sample(frac=train_size,random_state=200)
valid_dataset = train_df.drop(train_dataset.index).reset_index(drop=True)
train_dataset = train_dataset.reset_index(drop=True)
test_dataset  = test_df.reset_index(drop=True)

print("TRAIN Dataset: {}".format(train_dataset.shape))
print("VAL Dataset: {}".format(valid_dataset.shape))
print("TEST Dataset: {}".format(test_dataset.shape))

training_set = MultiLabelDataset(train_dataset, tokenizer, MAX_LEN)
validation_set = MultiLabelDataset(valid_dataset, tokenizer, MAX_LEN)
test_set = MultiLabelDataset(test_dataset, tokenizer, MAX_LEN)


# Load Data
train_params = {'batch_size': TRAIN_BATCH_SIZE,
                'shuffle': True,
                'num_workers': 0
                }

test_params = {'batch_size': VALID_BATCH_SIZE,
                'shuffle': True,
                'num_workers': 0
                }

training_loader = DataLoader(training_set, **train_params)
validation_loader = DataLoader(validation_set, **test_params)
testing_loader = DataLoader(test_set, **test_params)


# Creating the customized model, by adding a drop out and a dense layer on top of distil bert to get the final output for the model. 
class DistilBERTClass(torch.nn.Module):
    def __init__(self,num_tokens=30000, d_model=512):
        super(DistilBERTClass, self).__init__()
        self.l1 = DistilBertModel.from_pretrained("distilbert-base-uncased")
        self.embed = torch.nn.Embedding(num_tokens, d_model,sparse=True)
        self.pre_classifier = torch.nn.Linear(768, 768)
        self.dropout = torch.nn.Dropout(0.3)
        self.classifier = torch.nn.Linear(768, num_labels)

    def forward(self, input_ids, attention_mask):
        output_1 = self.l1(input_ids=input_ids, attention_mask=attention_mask)
        hidden_state = output_1[0]
        pooler = hidden_state[:, 0]
        pooler = self.pre_classifier(pooler)
        pooler = torch.nn.Tanh()(pooler)
        pooler = self.dropout(pooler)
        output = self.classifier(pooler)
        
        return output

model = DistilBERTClass()
model.to(device)
from util_loss import ResampleLoss


m = torch.nn.Sigmoid()
loss_func = torch.nn.BCELoss()
    
#Define Loss function 
def loss_fn(outputs, targets):
  return loss_func(m(outputs), targets)

optimizer = torch.optim.Adam(params =  model.parameters(), lr=LEARNING_RATE)

# Plot Val loss
import matplotlib.pyplot as plt
def loss_plot(epochs, loss):
    plt.plot(epochs, loss, color='red', label='loss')
    plt.xlabel("epochs")
    plt.title("validation loss")
    plt.savefig(dataset + "_val_loss.png")

# Train Model
def train_model(start_epochs,  n_epochs,
                training_loader, validation_loader, model,optimizer):
  
  loss_vals = []
  for epoch in range(start_epochs, n_epochs+1):
    train_loss = 0
    valid_loss = 0

    ######################    
    # Train the model #
    ######################

    model.train()
    
    print('############# Epoch {}: Training Start   #############'.format(epoch))
    for batch_idx, data in enumerate(training_loader):
        optimizer.zero_grad()

        #Forward
        ids = data['ids'].to(device, dtype = torch.long)
        targets = data['targets'].to(device, dtype = torch.float)
        #data['targets'].toarray()
        mask = data['mask'].to(device, dtype = torch.long)
        outputs = model(ids,mask)
        #Backward
        #loss = loss_func(outputs.view(-1,num_labels),targets.type_as(logits).view(-1,num_labels))
        loss = loss_fn(outputs.view(-1,num_labels),targets.type_as(outputs).view(-1,num_labels))
        #print("outputs.view(-1,num_labels):shape:{}{}".format(np.shape(outputs.view(-1,num_labels)),outputs.view(-1,num_labels)))
        #print("targets.type_as(outputs).view(-1,num_labels):shape:{}{}".format(np.shape(targets.type_as(outputs).view(-1,num_labels)),targets.type_as(outputs).view(-1,num_labels)))
        loss.backward()
        optimizer.step()
        train_loss = train_loss + ((1 / (batch_idx + 1)) * (loss.item() - train_loss))

        print("epoch:{}_______batch_index:{}".format(epoch,batch_idx))
        print("batch_index:{}".format(batch_idx))

    ######################    
    # Validate the model #
    ######################
 
    model.eval()
    
    with torch.no_grad():
      for batch_idx, data in enumerate(validation_loader, 0):
            ids = data['ids'].to(device, dtype = torch.long)
            targets = data['targets'].to(device, dtype = torch.float)
            mask = data['mask'].to(device, dtype = torch.long)
            outputs = model(ids,mask)

            loss = loss_fn(outputs.view(-1, num_labels), targets.type_as(outputs).view(-1, num_labels))
            valid_loss = valid_loss + ((1 / (batch_idx + 1)) * (loss.item() - valid_loss))

      # calculate average losses
      train_loss = train_loss/len(training_loader)
      valid_loss = valid_loss/len(validation_loader)
      # print training/validation statistics 
      print('Epoch: {} \tAverage Training Loss: {:.6f} \tAverage Validation Loss: {:.6f}'.format(
            epoch, 
            train_loss,
            valid_loss
            ))
      loss_vals.append(valid_loss)
    # Plot loss
  loss_plot(np.linspace(1, n_epochs, n_epochs).astype(int), loss_vals)
  return model

trained_model = train_model(1,epochs, training_loader, validation_loader, model, optimizer)

def validation(testing_loader):
    model.eval()
    fin_targets=[]
    fin_outputs=[]
    with torch.no_grad():
        for _, data in enumerate(testing_loader, 0):
            ids = data['ids'].to(device, dtype = torch.long)
            mask = data['mask'].to(device, dtype = torch.long)
            #token_type_ids = data['token_type_ids'].to(device, dtype = torch.long)
            targets = data['targets'].to(device, dtype = torch.float)
            outputs = model(ids, mask)
            fin_targets.extend(targets.cpu().detach().numpy().tolist())
            fin_outputs.extend(torch.sigmoid(outputs).cpu().detach().numpy().tolist())
    return fin_outputs, fin_targets

# Test Model
outputs, targets = validation(testing_loader)
outputs = np.array(outputs) >= 0.5
accuracy = metrics.accuracy_score(targets, outputs)
f1_score_avg = metrics.f1_score(targets, outputs, average='samples')
f1_score_micro = metrics.f1_score(targets, outputs, average='micro')
f1_score_macro = metrics.f1_score(targets, outputs, average='macro')
print(f"Accuracy Score = {accuracy}")
print(f"F1 Score (Samples) = {f1_score_avg}")
print(f"F1 Score (Micro) = {f1_score_micro}")
print(f"F1 Score (Macro) = {f1_score_macro}")

#Save results
with open(dataset + "_results.txt", "w") as f:
    print(f"F1 Score (Samples) = {f1_score_avg}",f"Accuracy Score = {accuracy}",f"F1 Score (Micro) = {f1_score_micro}",f"F1 Score (Macro) = {f1_score_macro}", file=f)
