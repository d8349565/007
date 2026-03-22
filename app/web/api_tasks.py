"""任务状态 API"""
from flask import Blueprint, jsonify
from app.services.task_tracker import get_processing_tasks

api_tasks_bp = Blueprint('api_tasks', __name__, url_prefix='/api/tasks')


@api_tasks_bp.route('/status', methods=['GET'])
def get_tasks_status():
    """获取当前处理中/失败的任务列表"""
    tasks, summary = get_processing_tasks()
    return jsonify({
        'tasks': tasks,
        'summary': summary
    })
