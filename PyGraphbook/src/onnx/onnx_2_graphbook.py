import argparse
import logging
import os
from collections import defaultdict
from typing import List, Dict, Set

from src import graph as graphbook
from src.onnx import onnx_helper
from src.onnx.onnx_helper import OnnxLink, OnnxGraph, OnnxOperation

FORWARD_SLASH = "/"
INPUT = "input"
INPUTS = "inputs"
OUTPUT = "output"
NAME = 'name'


def _calculate_composite_input_outputs(var_name: str, path1: str, path2: str, var_map: dict) -> None:
    """ Given two composite paths, calculate the inputs and outputs of the composite operations.

        This is done by looking at the links between the two composites and the operations in the two composites.

        Either the link is traveling downstream, upstream, or across stream.

        var_map should map inputs and outputs on each composite.

        :arg var_name: The variable name
        :arg path1: The first composite path
        :arg path2: The second composite path, not equal to path1
        :arg var_map: The variable map

        :returns: None
    """

    if not path1:
        # Going downstream from path1 to path2
        path2_split = path2.split(FORWARD_SLASH)
        for i in range(1, len(path2_split)):
            join_back = FORWARD_SLASH.join(path2_split[:i+1])
            if INPUT not in var_map[join_back]:
                var_map[join_back][INPUT] = []

            if var_name not in var_map[join_back][INPUT]:
                var_map[join_back][INPUT].append(var_name)
        return

    if not path2:
        # Then we are going upstream from path1 to path2.
        path1_split = path1.split(FORWARD_SLASH)
        for i in range(len(path1_split)):
            join_back = FORWARD_SLASH.join(path1_split[:i+1])
            if OUTPUT not in var_map[join_back]:
                var_map[join_back][OUTPUT] = []

            if var_name not in var_map[join_back][OUTPUT]:
                var_map[join_back][OUTPUT].append(var_name)
        return

    if path1.startswith(path2):
        """ e.g., /x/y/z and /x/y
            
            Then it is entirely upstream, because path1 is deeper than path2.
        """
        # Get the list that is just in path1 minus path2
        path1_split = path1.split(FORWARD_SLASH)
        path2_split = path2.split(FORWARD_SLASH)
        partial_split = path1_split[len(path2_split):]

        # Upstream means we are added outputs to each of the partial splits parts
        for i in range(len(partial_split)):
            join_back = path2 + FORWARD_SLASH + FORWARD_SLASH.join(partial_split[:i + 1])
            if OUTPUT not in var_map[join_back]:
                var_map[join_back][OUTPUT] = []

            if var_name not in var_map[join_back][OUTPUT]:
                var_map[join_back][OUTPUT].append(var_name)
        return

    if path2.startswith(path1):
        # The other way around.
        path1_split = path1.split(FORWARD_SLASH)
        path2_split = path2.split(FORWARD_SLASH)
        partial_split = path2_split[len(path1_split):]

        # Downstream means we are adding inputs to each of the partial splits parts
        for i in range(len(partial_split)):
            join_back = path1 + FORWARD_SLASH + FORWARD_SLASH.join(partial_split[:i + 1])

            if INPUT not in var_map[join_back]:
                var_map[join_back][INPUT] = []

            if var_name not in var_map[join_back][INPUT]:
                var_map[join_back][INPUT].append(var_name)
        return

    # If we're here, then there's a cross stream link.
    # For each composite on path in path1, needs output
    # For each composite on path in path2, needs input
    path1_split = path1.split(FORWARD_SLASH)
    path2_split = path2.split(FORWARD_SLASH)

    # Get the first n parts that are shared
    shared_parts = []
    for i in range(min(len(path1_split), len(path2_split))):
        if path1_split[:i+1] == path2_split[:i+1]:
            shared_parts.append(FORWARD_SLASH.join(path1_split[:i+1]))
        else:
            break

    # For each shared path, we'll do nothing, for each unique on composite path 1, we need output and for each unique on composite path 2, we need input.

    for i in range(len(path1_split)):
        join_back = FORWARD_SLASH.join(path1_split[:i+1])
        if join_back in shared_parts:
            continue
        if OUTPUT not in var_map[join_back]:
            var_map[join_back][OUTPUT] = []
        if var_name not in var_map[join_back][OUTPUT]:
            var_map[join_back][OUTPUT].append(var_name)

    for i in range(len(path2_split)):
        join_back = FORWARD_SLASH.join(path2_split[:i+1])
        if join_back in shared_parts:
            continue
        if INPUT not in var_map[join_back]:
            var_map[join_back][INPUT] = []
        if var_name not in var_map[join_back][INPUT]:
            var_map[join_back][INPUT].append(var_name)


def _get_graphbook_type_from_str(type_str: str) -> graphbook.DataType:
    if "(int" in type_str or "(uint" in type_str:
        return graphbook.DataType.INTEGER
    elif "(float" in type_str or "(double" in type_str or "(bfloat" in type_str:
        return graphbook.DataType.DECIMAL
    elif "(string" in type_str:
        return graphbook.DataType.TEXT
    elif "(bool" in type_str:
        return graphbook.DataType.BOOLEAN
    else:
        return graphbook.DataType.NULL


def onnx_op_to_graphbook(onnx_op: OnnxOperation) -> graphbook.Operation:
    """ converts onnx operation to graphbook operation"""

    graphbook_inputs = []
    if onnx_op.input:
        for i, inp in enumerate(onnx_op.input):
            graphbook_var = graphbook.Variable(name=inp)
            if not onnx_op.op_type_meta_data:
                # Then it's our own read or write file
                if onnx_op.opType == "read_from_file":
                    if i == 0:
                        graphbook_var.primitive_name = "file_name"
                    elif i == 1:
                        graphbook_var.primitive_name = "dir_name"
                    elif i == 2:
                        graphbook_var.primitive_name = "extraction_schema"

            else:
                input_meta = list(onnx_op.op_type_meta_data[INPUTS])
                if input_meta and i >= len(input_meta):
                    var_meta = dict(input_meta[0])
                    if "list" in var_meta:
                        # Then it's a list, for example for concat.
                        graphbook_var.primitive_name = f"list_item_{i}"
                else:
                    var_meta = dict(input_meta[i])
                    if NAME in var_meta:
                        graphbook_var.primitive_name = var_meta[NAME]
            graphbook_inputs.append(graphbook_var)


    attribute_names = []
    if onnx_op.attribute:
        attribute_names = [attribute.name for attribute in onnx_op.attribute]

    if onnx_op.op_type_meta_data and "attribute" in onnx_op.op_type_meta_data:
        for i, attribute in onnx_op.op_type_meta_data["attribute"]:
            graphbook_var = graphbook.Variable(name=attribute[NAME], primitive_name="attribute_" + attribute[NAME])
            """ 
            Then we need to specify that it's "filled" in this operation
            This is because attributes in onnx act a bit like a conditional sometimes. 
            For example, for Constant operation, there is an attribute for each value type and shape it can take. 
            We add each attribute as an input and specify whether it is filled on this operation.
            Then later we can map based on the unique qualities of the operation and how it maps to graphbook.
            """
            graphbook_var.onnx_attribute = attribute[NAME] in attribute_names

            if "type" in attribute:
                # This is a tensor
                graphbook_var.type = _get_graphbook_type_from_str(str(attribute['type']))
            elif attribute.i:
                graphbook_var.type = graphbook.DataType.INTEGER
            elif attribute.ints:
                graphbook_var.type = graphbook.DataType.INTEGER
                graphbook_var.shape = [len(attribute.ints)]

            graphbook_inputs.append(graphbook_var)

    graphbook_outputs = []
    if onnx_op.output:
        for i, out in enumerate(onnx_op.output):
            graphbook_var = graphbook.Variable(name=out)
            if not onnx_op.op_type_meta_data:
                # Then it's our own read or write file
                if onnx_op.opType == "write_to_file":
                    if i == 0:
                        graphbook_var.primitive_name = "file_name"
                    elif i == 1:
                        graphbook_var.primitive_name = "dir_name"
                    elif i == 2:
                        graphbook_var.primitive_name = "overwrite"
                    elif i == 3:
                        graphbook_var.primitive_name = "data"

            else:
                var_meta = onnx_op.op_type_meta_data["outputs"][i]
                if NAME in var_meta:
                    graphbook_var.primitive_name = var_meta[NAME]

            graphbook_outputs.append(graphbook_var)

    # For now, we won't say it's a primitive operation since it's not mapped yet to a real primitive.
    graphbook_op_type = graphbook.OperationType.COMPOSITE_OPERATION
    if onnx_op.opType in ["read_from_file", "write_to_file"]:
        graphbook_op_type = graphbook.OperationType.PRIMITIVE_OPERATION

    # TODO: Add mapping here from onnx optype to graphbook schema type.
    return graphbook.Operation(
        name=onnx_op.name,
        primitive_name=onnx_op.opType,
        type=graphbook_op_type,
        inputs=graphbook_inputs,
        outputs=graphbook_outputs
    )


def _compile_onnx_composite_map(onnx_graph: OnnxGraph, composite_names: Set[str]) -> Dict[str, List[OnnxOperation]]:
    """ Compiles the composite map for the onnx graph.

        This is a map from composite path to list of operations in that composite.
    """
    composite_map = defaultdict(list)
    for op in onnx_graph.onnx_ops:
        if op.composite_path:

            split_path = op.composite_path.split("/")
            for i, part in enumerate(split_path):
                join_back = "/".join(split_path[:i + 1])
                composite_names.add(join_back)

            composite_map[op.composite_path].append(op)
        else:
            composite_map[onnx_graph.name].append(op)

    return composite_map


def _compile_onnx_composite_link_map(
        onnx_graph: OnnxGraph,
        name_to_op: Dict[str, OnnxOperation],
        # primitive_to_final_output_links: Set[OnnxLink],
        composite_var_map: dict) -> Dict[str, List[OnnxLink]]:
    """ Compiles the composite link map for the onnx graph.

        This is a map from composite path to list of links in that composite.
    """
    composite_link_map = defaultdict(list)

    for link in onnx_graph.onnx_links:
        if link.sink == onnx_graph.name:
            # Then this is a final output
            composite_link_map[onnx_graph.name].append(link)
            _calculate_composite_input_outputs(
                var_name=link.var_name,
                path1=name_to_op[link.source].composite_path,
                path2="",
                var_map=composite_var_map)

        elif link.source in name_to_op and link.var_name in onnx_graph.outputs:
            # Then this is a final output
            continue
            # primitive_to_final_output_links.add(link)

        elif link.sink in name_to_op:
            if name_to_op[link.sink].composite_path:
                composite_link_map[name_to_op[link.sink].composite_path].append(link)
                if link.source == onnx_graph.name:
                    _calculate_composite_input_outputs(
                        var_name=link.var_name,
                        path1="",
                        path2=name_to_op[link.sink].composite_path,
                        var_map=composite_var_map)
                elif not name_to_op[link.source].composite_path \
                        or name_to_op[link.sink].composite_path != name_to_op[link.source].composite_path:
                    # The link is traversing graph levels, so it should be an input to each composite along the path
                    _calculate_composite_input_outputs(
                        var_name=link.var_name,
                        path1=name_to_op[link.source].composite_path,
                        path2=name_to_op[link.sink].composite_path,
                        var_map=composite_var_map
                    )
            elif link.source == onnx_graph.name:
                composite_link_map[onnx_graph.name].append(link)
            elif name_to_op[link.source].composite_path:
                # Then we are going from composite to non-composite
                composite_link_map[onnx_graph.name].append(link)
                _calculate_composite_input_outputs(
                    var_name=link.var_name,
                    path1=name_to_op[link.source].composite_path,
                    path2=name_to_op[link.sink].composite_path,
                    var_map=composite_var_map
                )
            else:
                composite_link_map[onnx_graph.name].append(link)
        else:
            # Then something fishy is happening. Why is this link not there?
            raise ValueError(f"Link sink {link.sink} not recognized")

    return composite_link_map


def _compile_graphbook_operations_from_composite_map(
        onnx_graph: OnnxGraph,
        composite_map: Dict[str, List[OnnxOperation]],
        composite_names: Set[str],
        primitive_map: Dict[str, graphbook.Operation],
        composite_var_map: dict) -> Dict[str, graphbook.Operation]:
    """ Compiles the graphbook operations from the composite map.
    """

    graphbook_composite_map = {}
    already_visited_root = False

    for name in composite_names:
        if not name:
            if already_visited_root:
                # Then we've already been here.
                continue
            if onnx_graph.name not in composite_map:
                composite_map[onnx_graph.name] = []
            if name in composite_map:
                composite_map[onnx_graph.name].extend(composite_map[name])

            already_visited_root = True
            name = onnx_graph.name
            inputs = list(onnx_graph.inputs)
            outputs = list(onnx_graph.outputs)
        else:
            if INPUT not in composite_var_map[name]:
                composite_var_map[name][INPUT] = []
            inputs = composite_var_map[name][INPUT]

            if OUTPUT not in composite_var_map[name]:
                composite_var_map[name][OUTPUT] = []
            outputs = composite_var_map[name][OUTPUT]

        this_primitive = {}
        if name in composite_map:
            this_primitive = {
                onnx_op.name: onnx_op_to_graphbook(onnx_op)
                for onnx_op in composite_map[name]
            }
            primitive_map.update(this_primitive)

        graphbook_composite_map[name] = graphbook.Operation(
            name=name,
            primitive_name=name,
            type=graphbook.OperationType.COMPOSITE_OPERATION,
            operations=list(this_primitive.values()),
            inputs=[graphbook.Variable(name=inp) for inp in inputs],
            outputs=[graphbook.Variable(name=out) for out in outputs],
            links=[]
        )

    return graphbook_composite_map


def _compile_links_between_composite(
        onnx_graph: OnnxGraph,
        graphbook_composite_map: Dict[str, graphbook.Operation],
        composite_names: Set[str]) -> None:
    # Links between composites only.
    for name in composite_names:
        if name == onnx_graph.name:
            continue
        split_name = name.split(FORWARD_SLASH)
        if len(split_name) <= 1:
            continue

        # Then it is a sub-operation of some composite
        parent_name = FORWARD_SLASH.join(split_name[:-1])
        if len(parent_name) == 0:
            parent_name = onnx_graph.name

        sub_op = graphbook_composite_map[name]
        parent_composite = graphbook_composite_map[parent_name]
        parent_composite.operations.append(sub_op)

        for inp in sub_op.inputs:
            for comp_inp in parent_composite.inputs:
                if inp.name == comp_inp.name:
                    # add link
                    parent_composite.links.append(graphbook.Link(
                        source=graphbook.LinkEndpoint(operation="this", data=inp.name),
                        sink=graphbook.LinkEndpoint(operation=name, data=inp.name),
                    ))
        for out in sub_op.outputs:
            for comp_out in parent_composite.outputs:
                if out.name == comp_out.name:
                    parent_composite.links.append(graphbook.Link(
                        source=graphbook.LinkEndpoint(operation=name, data=out.name),
                        sink=graphbook.LinkEndpoint(operation="this", data=out.name),
                    ))


def _compile_links_between_composite_and_primitive(
        onnx_graph: OnnxGraph,
        graphbook_composite_map: Dict[str, graphbook.Operation],
        primitive_map: Dict[str, graphbook.Operation],
        composite_link_map: Dict[str, List[OnnxLink]]) -> None:

    # This is where links are connected between composites to primitives.
    for composite_name, link_list in composite_link_map.items():

        composite = graphbook_composite_map[composite_name]

        # For each link that ends in this composite graph, create a path of links from the source to here.
        for link in link_list:
            if link.source == onnx_graph.name:
                continue

            # Get the source and sink operations
            primitive_source = primitive_map[link.source]

            if primitive_source in composite.operations:
                # Then it's simply adding a link in this graph.
                composite.links.append(graphbook.Link(
                    source=graphbook.LinkEndpoint(operation=link.source, data=link.var_name),
                    sink=graphbook.LinkEndpoint(operation=link.sink, data=link.var_name),
                    var_name=link.var_name
                ))
            elif composite_name == onnx_graph.name:

                sink_name = FORWARD_SLASH.join(primitive_source.name.split(FORWARD_SLASH)[:-1])
                next_composite = graphbook_composite_map[sink_name]
                #
                if link.var_name not in [out.name for out in next_composite.outputs]:
                    raise ValueError("Expected link var name to be in composite outputs")
                next_composite.links.append(graphbook.Link(
                    source=graphbook.LinkEndpoint(operation=primitive_source.name, data=link.var_name),
                    sink=graphbook.LinkEndpoint(operation="this", data=link.var_name),
                ))
            elif not primitive_source.name.startswith(composite_name):
                # If the primitive source is not from within this graph, it must be coming from parent graph.
                if link.var_name not in [inp.name for inp in composite.inputs]:
                    raise ValueError("Expected link var name to be in composite inputs")

                composite.links.append(graphbook.Link(
                    source=graphbook.LinkEndpoint(operation="this", data=link.var_name),
                    sink=graphbook.LinkEndpoint(operation=link.sink, data=link.var_name),
                ))

            # If it comes from within this graph, then we need to find the composite that produces it.
            else:
                # Get the next item in the path of primitive_source.name after composite_name
                split_name = primitive_source.name.split(FORWARD_SLASH)
                split_composite_name = composite_name.split(FORWARD_SLASH)
                if len(split_name) <= len(split_composite_name):
                    raise ValueError("Primitive source name is not longer than composite name")

                next_composite_name = FORWARD_SLASH.join(split_name[:len(split_composite_name) + 1])
                next_composite = graphbook_composite_map[next_composite_name]

                if link.var_name not in [out.name for out in next_composite.outputs]:
                    raise ValueError("Expected link var name to be in composite outputs")
                composite.links.append(graphbook.Link(
                    source=graphbook.LinkEndpoint(operation=next_composite_name, data=link.var_name),
                    sink=graphbook.LinkEndpoint(operation=link.sink, data=link.var_name),
                ))

def onnx_graph_to_graphbook(onnx_graph: OnnxGraph) -> graphbook.Operation:
    """Converts onnx graph to Graphbook graph"""

    # First, get all the unique composite operations and assign the ops to the right operations.
    composite_names = {''}
    composite_map = _compile_onnx_composite_map(onnx_graph, composite_names)
    name_to_op = {op.name: op for op in onnx_graph.onnx_ops}

    composite_var_map = defaultdict(dict)
    # primitive_to_final_output_links = set()

    # Compile onnx composite inputs and outputs, and collects links
    composite_link_map = _compile_onnx_composite_link_map(
        onnx_graph=onnx_graph,
        name_to_op=name_to_op,
        # primitive_to_final_output_links=primitive_to_final_output_links,
        composite_var_map=composite_var_map
    )

    primitive_map = {}

    # Create graphbook operations for all onnx operations
    graphbook_composite_map = _compile_graphbook_operations_from_composite_map(
        onnx_graph=onnx_graph,
        composite_map=composite_map,
        composite_names=composite_names,
        primitive_map=primitive_map,
        composite_var_map=composite_var_map
    )

    # Compile links between composites
    _compile_links_between_composite(
        onnx_graph=onnx_graph,
        graphbook_composite_map=graphbook_composite_map,
        composite_names=composite_names
    )

    # Compile links between composites and primitives
    _compile_links_between_composite_and_primitive(
        onnx_graph=onnx_graph,
        graphbook_composite_map=graphbook_composite_map,
        primitive_map=primitive_map,
        composite_link_map=composite_link_map
    )

    return graphbook_composite_map[onnx_graph.name]


if __name__ == "__main__":

    # Add argparse options
    argparse = argparse.ArgumentParser()
    argparse.add_argument("--onnx_folder", type=str, default="flan-t5-small-onnx")
    argparse.add_argument("--onnx_file", type=str, required=False,
                          default="flan-t5-small-onnx/encoder_model.onnx",
                          help="If onnx_file specified, then this is the onnx file to convert.")
    argparse.add_argument("--output_folder", type=str, default="flan-t5-small-graphbook")
    argparse.add_argument("--logging", type=str, default="INFO")
    args = argparse.parse_args()

    logging.basicConfig(level=args.logging)

    if args.onnx_file:
        onnx_list = [onnx_helper.onnx_to_graph(os.path.join(f"{args.onnx_file}"))]
    else:
        # Convert onnx to graphbook
        onnx_list = onnx_helper.onnx_folder_to_onnx_list(args.onnx_folder)

    logging.info('Generated onnx graphs, now converting to Graphbook')

    for graph in onnx_list:
        logging.info("Converting: " + graph.name)
        graphbook_root = onnx_graph_to_graphbook(graph)

        logging.info("Generated: " + graphbook_root.name)

        json_str = graphbook_root.model_dump_json(indent=4, exclude_unset=True, exclude_none=True)

        # Create directory if doesn't exist
        if not os.path.exists(args.output_folder):
            os.makedirs(args.output_folder)

        with open(f"{args.output_folder}/{graphbook_root.name.split('/')[-1]}.json", "w") as f:
            f.write(json_str)