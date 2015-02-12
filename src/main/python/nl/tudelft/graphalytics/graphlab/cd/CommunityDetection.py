import sys
import os

import graphlab as gl
from graphlab.deploy.environment import Hadoop
import time


__author__ = 'Jorai Rijsdijk'


def create_environment(hadoop_home, memory_mb, virtual_cores):
    """
    Create a (distributed) Hadoop environment with the given hadoop_home, memory_mb and virtual_cores

    :param hadoop_home: The location of the hadoop_home to get the hadoop config files from hadoop_home/etc/hadoop
    :param memory_mb: The amount of memory to use for processing the algorithm
    :param virtual_cores: The amount of virtual cores to use for graph processing
    :return: The created Hadoop environment object
    """
    return gl.deploy.environment.Hadoop('Hadoop', config_dir=hadoop_home + '/etc/hadoop', memory_mb=memory_mb,
                                        virtual_cores=virtual_cores, gl_source=None)


if len(sys.argv) < 8:
    print >> sys.stderr, "Too few arguments, need at least 7: <use_hadoop> [virtual_cores] [heap_size] <graph_file> <directed> <edge_based> <node_preference> <hop_attenuation> <max_iterations>"
    exit(1)

# Read arguments
use_hadoop = sys.argv[1] == "true"

if use_hadoop:
    if len(sys.argv) < 10:
        print >> sys.stderr, "Too few arguments for use_hadoop=true, need at least 9: <use_hadoop=true> <virtual_cores> <heap_size> <graph_file> <directed> <edge_based> <node_preference> <hop_attenuation> <max_iterations>"
        exit(1)
    else:
        virtual_cores = sys.argv[2]
        heap_size = sys.argv[3]
        graph_file = sys.argv[4]
        directed = sys.argv[5] == "true"
        edge_based = sys.argv[6] == "true"
        node_preference = float(sys.argv[6])
        hop_attenuation = float(sys.argv[7])
        max_iterations = int(sys.argv[8])
        # Create hadoop environment object
        hadoop_home = os.environ.get('HADOOP_HOME')
        hadoop = create_environment(hadoop_home=hadoop_home, memory_mb=heap_size, virtual_cores=virtual_cores)
else:
    graph_file = sys.argv[2]
    directed = sys.argv[3] == "true"
    edge_based = sys.argv[4] == "true"
    node_preference = float(sys.argv[5])
    hop_attenuation = float(sys.argv[6])
    max_iterations = int(sys.argv[7])

if not edge_based:
    print >> sys.stderr, "Vertex based graph format not supported yet"
    exit(2)


def load_graph_task(task):
    import graphlab as gl_

    graph_data = gl_.SFrame.read_csv(task.params['csv'], header=False, delimiter=' ', column_type_hints=long)
    graph = gl.SGraph().add_edges(graph_data, src_field='X1', dst_field='X2')

    if not task.params['directed']:
        graph.add_edges(graph_data, src_field='X2', dst_field='X1')

    task.outputs['graph'] = graph


def community_detection_model(task):
    def count_edges(src, edge, dst):
        src['edges'] += 1
        dst['edges'] += 1
        return src, edge, dst

    def community_detection_propagate(src, edge, dst):
        # Handle the outgoing edge
        if dst['score'] < src['weighted_score']:
            dst['label'] = src['label']
            dst['score'] = src['weighted_score'] - hop_attenuation
        # Handle the incoming edge
        if src['score'] < dst['weighted_score']:
            src['label'] = dst['label']
            src['score'] = dst['weighted_score'] - hop_attenuation
        return src, edge, dst

    graph = task.inputs['data']

    # Count the amount of edges per vertex
    graph.vertices['edges'] = 0
    print "Calculating edge counts per vertex"
    graph = graph.triple_apply(count_edges, ['edges'], ['edges'])

    graph.vertices['label'] = graph.vertices['__id'].apply(lambda x: x)

    iteration = 0
    while iteration < max_iterations:
        print 'Start iteration %d' % (iteration + 1)
        graph.vertices['score'] = 1.0
        graph.vertices['weighted_score'] = graph.vertices.apply(lambda x: x['score'] * (x['edges'] ** node_preference))
        graph = graph.triple_apply(community_detection_propagate, ['label', 'score', 'weighted_score', 'edges'])
        iteration += 1

    task.outputs['cd_graph'] = graph

if use_hadoop:  # Deployed execution
    # Define the graph loading task
    load_graph = gl.deploy.Task('load_graph')
    load_graph.set_params({'csv': graph_file, 'directed': directed})
    load_graph.set_code(load_graph_task)
    load_graph.set_outputs(['graph'])

    # Define the shortest_path model create task
    community = gl.deploy.Task('community_detection')
    community.set_params({'node_preference': node_preference, 'hop_attenuation': hop_attenuation, 'max_iterations': max_iterations})
    community.set_inputs({'data': ('load_graph', 'graph')})
    community.set_code(community_detection_model)
    community.set_outputs(['cd_graph'])

    # Create the job and deploy it to the Hadoop cluster
    hadoop_job = gl.deploy.job.create(['load_graph', 'community_detection'], environment=hadoop)
    while hadoop_job.get_status() in ['Pending', 'Running']:
        time.sleep(2)  # sleep for 2s while polling for job to be complete.

    output_graph = community.outputs['cd_graph']
else:  # Local execution
    # Stub task class
    class Task:
        def __init__(self, **keywords):
            self.__dict__.update(keywords)

    # Stub task object to keep function definitions intact
    cur_task = Task(params={'csv': graph_file, 'directed': directed, 'node_preference': node_preference, 'hop_attenuation': hop_attenuation,
                            'max_iterations': max_iterations}, inputs={}, outputs={})

    load_graph_task(cur_task)
    cur_task.inputs['data'] = cur_task.outputs['graph']
    community_detection_model(cur_task)
    output_graph = cur_task.outputs['cd_graph']