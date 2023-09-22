""" Take a dataset for a graph, and a vocab set, and deconstruct it back into graph form. """

import json
import os
from typing import List

from src import graph_util
from src.dataset import construct_dataset


def _deconstruct_dataset(
    top_op_name: str,
    dataset: construct_dataset.HierarchicalDataset,
    var_row: List[int],
    graph_level: List[int],
    vocab: dict,
    level_to_op: dict,
) -> graph_util.Operation:
    """ Deconstruct a dataset into a graph, hierarchically"""

    # This is a new op
    top_op = graph_util.Operation(name=top_op_name, primitive_name=top_op_name, type=graph_util.OperationType.COMPOSITE_OPERATION)

    index_to_op = {}

    ops = []

    # This is tracking as per var.
    op_name_list = []

    last_op_name = None
    last_op = None
    last_was_input = True

    names = set()

    for i, (var_item, level_item) in enumerate(zip(var_row, graph_level)):
        if level_item == construct_dataset.DUMMY_LEVEL:
            # Then we're in sub-graph dummy and don't need to do anything
            op_name_list.append("this")
            continue

        op_name, is_input, var_name = vocab[var_item]

        if op_name.startswith("DataType"):
            # Then this is a bootstrapped data and we'll save it for later when we do adj matrix.
            # TODO: store details of tensor.
            op_name_list.append("bootstrap")
            # Doesn't count as last.
            continue

        variable = graph_util.Variable(name=var_name, primitive_name=var_name)

        if level_item == construct_dataset.PRIMITIVE_LEVEL:

            # Then it's primitive and we can add it to the graph.
            if last_op_name and last_op_name == op_name and (is_input == last_was_input or last_was_input):
                    # If last was output,
                # TODO: There will need to be an exception here for write_to_database...
                # Then it's continuation of same primitive operation.
                op = last_op
            else:
                # This is a new operation.
                name = op_name
                if op_name in names:
                    # Then we've already seen this op name, so we need to create a new one.
                    name = f"{op_name}_{i}"
                op = graph_util.Operation(name=name, primitive_name=op_name,
                                          type=graph_util.OperationType.PRIMITIVE_OPERATION)
                ops.append(op)

            names.add(op.name)
            last_op_name = op_name

            if is_input:
                op.inputs.append(variable)
            else:
                op.outputs.append(variable)

            last_was_input = is_input

        else:
            # Then it's referring to a new graph level. We should recursively call this function.
            if level_item in level_to_op:
                # Then we've seen this op, so just add input/output
                op = level_to_op[level_item]

            else:
                op = _deconstruct_dataset(
                    top_op_name=last_op.name,
                    dataset=dataset,
                    var_row=dataset.variables[level_item],
                    graph_level=dataset.graph_level_ids[level_item],
                    vocab=vocab,
                    level_to_op=level_to_op,
                )
                ops.append(op)
                level_to_op[level_item] = op

            if is_input:
                level_to_op[level_item].inputs.append(variable)
            else:
                level_to_op[level_item].outputs.append(variable)

            last_was_input = True
            last_op_name = op.name

        index_to_op[i] = op
        last_op = op
        op_name_list.append(op.name)

    top_op.operations = ops
    return top_op

def deconstruct_dataset(
    dataset: construct_dataset.HierarchicalDataset,
    vocab_: dict
) -> graph_util.Operation:
    """ Deconstruct a dataset into a graph. """

    var_row = dataset.variables[0]
    graph_level = dataset.graph_level_ids[0]

    return _deconstruct_dataset(
        top_op_name=dataset.name,
        dataset=dataset,
        var_row=var_row,
        graph_level=graph_level,
        vocab=vocab_,
        level_to_op={},
    )

if __name__ == """__main__""":
    with open(os.getcwd() + "/graphbook_dataset/vocab.json", "r") as f:
        vocab = json.load(f)

    # convert vocab which is a nested list into a dict
    vocab = {vocab_level[0]: vocab_level[1] for vocab_level in vocab}

    with open(os.getcwd() + "/graphbook_dataset/graphs/Softmax.json", "r") as f:
        dataset_json = json.load(f)
        dataset_obj = construct_dataset.HierarchicalDataset.model_validate(dataset_json)
        print(f"{dataset_obj.name}, Longest sequence:"
              f" {construct_dataset.calculate_longest_sequence(dataset_obj)}, "
              f"Number of graph levels: {len(dataset_obj.graph_level_ids)}")

    # Now let's take dataset_obj and use the vocab to turn it into a graph_util.Operation object.
    op = deconstruct_dataset(dataset_obj, vocab)

    print(op)