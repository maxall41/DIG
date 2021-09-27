import os
import torch
import hydra
from omegaconf import OmegaConf
from benchmarks.utils import check_dir
from benchmarks.gnnNets import get_gnnNets
from benchmarks.dataset import get_dataset, get_dataloader
from dig.xgraph.evaluation import XCollector
from dig.xgraph.method import GNN_LRP


@hydra.main(config_path="config", config_name="config")
def pipeline(config):
    config.models.gnn_saving_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'checkpoints')
    config.explainers.explanation_result_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
    config.models.param = config.models.param[config.datasets.dataset_name]
    config.explainers.param = config.explainers.param[config.datasets.dataset_name]
    print(OmegaConf.to_yaml(config))

    if torch.cuda.is_available():
        device = torch.device('cuda', index=config.device_id)
    else:
        device = torch.device('cpu')

    # bbbp warning
    dataset = get_dataset(config.datasets.dataset_root,
                          config.datasets.dataset_name)
    dataset.data.x = dataset.data.x.float()
    dataset.data.y = dataset.data.y.squeeze().long()
    if config.models.param.graph_classification:
        dataloader_params = {'batch_size': config.models.param.batch_size,
                             'random_split_flag': config.datasets.random_split_flag,
                             'data_split_ratio': config.datasets.data_split_ratio,
                             'seed': config.datasets.seed}
        loader = get_dataloader(dataset, **dataloader_params)
        test_indices = loader['test'].dataset.indices
    else:
        node_indices_mask = (dataset.data.y != 0) * dataset.data.test_mask
        node_indices = torch.where(node_indices_mask)[0]

    model = get_gnnNets(input_dim=dataset.num_node_features,
                        output_dim=dataset.num_classes,
                        model_config=config.models)

    state_dict = torch.load(os.path.join(config.models.gnn_saving_dir,
                                         config.datasets.dataset_name,
                                         f"{config.models.gnn_name}_"
                                         f"{len(config.models.param.gnn_latent_dim)}l_best.pth"))['net']
    model.load_state_dict(state_dict)

    model.to(device)
    explanation_saving_dir = os.path.join(config.explainers.explanation_result_dir,
                                          config.datasets.dataset_name,
                                          config.models.gnn_name,
                                          'GNNLRP')
    check_dir(explanation_saving_dir)

    gnnlrp_explainer = GNN_LRP(model, explain_graph=config.models.param.graph_classification)

    index = 0
    x_collector = XCollector()
    if config.models.param.graph_classification:
        for i, data in enumerate(dataset[test_indices]):
            index += 1
            data.to(device)
            if os.path.isfile(os.path.join(explanation_saving_dir, f'example_{test_indices[i]}.pt')):
                walks = torch.load(os.path.join(explanation_saving_dir, f'example_{test_indices[i]}.pt'))
                walks = {k: v.to(device) for k, v in walks.items()}
                print(f"load example {test_indices[i]}.")
                walks, masks, related_preds = \
                    gnnlrp_explainer(data.x, data.edge_index,
                                     sparsity=config.explainers.sparsity,
                                     num_classes=dataset.num_classes,
                                     edge_masks=walks)
            else:
                print(f"GNNLRP explain example {test_indices[i]}.")
                walks, masks, related_preds = \
                    gnnlrp_explainer(data.x, data.edge_index,
                                     sparsity=config.explainers.sparsity,
                                     num_classes=dataset.num_classes)

                walks = {k: v.to('cpu') for k, v in walks.items()}
                torch.save(walks, os.path.join(explanation_saving_dir, f'example_{test_indices[i]}.pt'))

            prediction = model(data).argmax(-1).item()
            x_collector.collect_data(masks, related_preds, label=prediction)
    else:
        data = dataset.data
        data.to(device)
        prediction = model(data).argmax(-1)
        for node_idx in node_indices:
            if os.path.isfile(os.path.join(explanation_saving_dir, f'example_{node_idx}.pt')):
                walks = torch.load(os.path.join(explanation_saving_dir, f'example_{node_idx}.pt'))
                walks = {k: v.to(device) for k, v in walks.items()}
                print(f"load example {node_idx}.")
                walks, masks, related_preds = \
                    gnnlrp_explainer(data.x, data.edge_index,
                                     node_idx=node_idx,
                                     sparsity=config.explainers.sparsity,
                                     num_classes=dataset.num_classes,
                                     walks=walks)
            else:
                walks, masks, related_preds = \
                    gnnlrp_explainer(data.x, data.edge_index,
                                     node_idx=node_idx,
                                     sparsity=config.explainers.sparsity,
                                     num_classes=dataset.num_classes)
                walks = {k: v.to('cpu') for k, v in walks.items()}
                torch.save(walks, os.path.join(explanation_saving_dir, f'example_{node_idx}.pt'))
            x_collector.collect_data(masks, related_preds, label=prediction[node_idx].item())

    print(f'Fidelity: {x_collector.fidelity:.4f}\n'
          f'Fidelity_inv: {x_collector.fidelity_inv: .4f}\n'
          f'Sparsity: {x_collector.sparsity:.4f}')


if __name__ == '__main__':
    import sys
    sys.argv.append('explainers=gn_lrp')
    pipeline()
