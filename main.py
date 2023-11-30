import hydra
import numpy as np 
import json
import logging 
import matplotlib.pyplot as plt
import os
import openai
import re
import subprocess
from pathlib import Path
import shutil
import time 
from pprint import pprint

from utils.utils import * 

ROOT_DIR = os.getcwd()

@hydra.main(config_path="cfg", config_name="config", version_base="1.1")
def main(cfg):
    workspace_dir = Path.cwd()
    logging.info(f"Workspace: {workspace_dir}")
    logging.info(f"Project Root: {ROOT_DIR}")

    openai.api_key = os.getenv("OPENAI_API_KEY")

    problem = cfg.problem.problem_name
    problem_size = cfg.problem.problem_size
    problem_description = cfg.problem.description
    suffix = cfg.suffix
    model = cfg.model
    logging.info(f"Using LLM: {model}")
    logging.info("Problem: " + problem)
    logging.info("Problem description: " + problem_description)

    output_file = f"{ROOT_DIR}/problems/{problem}/{suffix.lower()}.py"

    # Loading all text prompts
    prompt_dir = f'{ROOT_DIR}/utils/prompts_{cfg.problem_type}'
    problem_dir = f"{ROOT_DIR}/problems/{problem}"
    initial_system = file_to_string(f'{prompt_dir}/initial_system.txt')
    code_output_tip = file_to_string(f'{prompt_dir}/code_output_tip.txt')
    code_feedback = file_to_string(f'{prompt_dir}/code_feedback.txt')
    initial_user = file_to_string(f'{prompt_dir}/initial_user.txt')
    func_signature = file_to_string(f'{problem_dir}/func_signature.txt')
    policy_feedback = file_to_string(f'{prompt_dir}/policy_feedback.txt')
    execution_error_feedback = file_to_string(f'{prompt_dir}/execution_error_feedback.txt')

    initial_system = initial_system.format(func_signature=func_signature) + code_output_tip
    initial_user = initial_user.format(problem_description=problem_description)
    messages = [{"role": "system", "content": initial_system}, {"role": "user", "content": initial_user}]
    
    print('######## Prompts: ########')
    pprint(messages[0])
    pprint(messages[1])

    
    best_obj_overall = float('inf')
    
    # Generation loop
    for iter in range(cfg.iteration):
        # Get response
        responses = []
        response_cur = None
        total_samples = 0
        total_token = 0
        total_completion_token = 0
        chunk_size = cfg.sample if "gpt-3.5" in model else 4

        logging.info(f"Iteration {iter}: Generating {cfg.sample} samples with {cfg.model}")

        while True:
            if total_samples >= cfg.sample:
                break
            for attempt in range(1000):
                try:
                    response_cur = openai.ChatCompletion.create(
                        model=model,
                        messages=messages,
                        temperature=cfg.temperature,
                        n=chunk_size
                    )
                    total_samples += chunk_size
                    break
                except Exception as e:
                    if attempt >= 10:
                        chunk_size = max(int(chunk_size / 2), 1)
                        print("Current Chunk Size", chunk_size)
                    logging.info(f"Attempt {attempt+1} failed with error: {e}")
                    time.sleep(1)
            if response_cur is None:
                logging.info("Code terminated due to too many failed attempts!")
                exit()

            responses.extend(response_cur["choices"])
            prompt_tokens = response_cur["usage"]["prompt_tokens"]
            total_completion_token += response_cur["usage"]["completion_tokens"]
            total_token += response_cur["usage"]["total_tokens"]

        # Logging Token Information
        logging.info(f"Iteration {iter}: Prompt Tokens: {prompt_tokens}, Completion Tokens: {total_completion_token}, Total Tokens: {total_token}")
        
        code_runs = [] 
        inner_runs = []
        for response_id in range(cfg.sample):
            response_cur = responses[response_id]["message"]["content"]
            logging.info(f"Iteration {iter}: GPT Output:\n " + response_cur)
            logging.info(f"Iteration {iter}: Processing Code Run {response_id}")

            # Regex patterns to extract python code enclosed in GPT response
            pattern = r'```python(.*?)```'
            code_string = re.search(pattern, response_cur, re.DOTALL)
            if code_string is not None:
                code_string = code_string.group(1).strip()
            
            code_string = response_cur if not code_string else code_string
            code_runs.append(code_string)

            with open(output_file, 'w') as file:
                file.writelines(code_string + '\n')

            # Copy the generated code to hydra output directory for bookkeeping
            shutil.copy(output_file, f"problem_iter{iter}_response{response_id}.py")

            # Execute the python file with flags
            stdout_filepath = f"problem_iter{iter}_response{response_id}.txt"
            with open(stdout_filepath, 'w') as f:
                process = subprocess.Popen(['python', '-u', f'{ROOT_DIR}/problems/{problem}/test.py', f'{problem_size}', ROOT_DIR],
                                            stdout=f, stderr=f)

            block_until_running(stdout_filepath, log_status=True, iter_num=iter, response_id=response_id)
            inner_runs.append(process)
        
        # Gather results
        code_paths = []
        objs = []
        exec_success = False 
        for response_id, (code_run, rl_run) in enumerate(zip(code_runs, inner_runs)):
            rl_run.communicate()
            stdout_filepath = f"problem_iter{iter}_response{response_id}.txt"
            code_paths.append(f"problem_iter{iter}_response{response_id}.py")

            with open(stdout_filepath, 'r') as f:
                stdout_str = f.read() 

            # content = ''
            # traceback_msg = filter_traceback(stdout_str)
            
            # read the last line of stdout_str
            try:
                obj = float(stdout_str.split('\n')[-2])
                objs.append(obj) # the smaller the better
            except:
                pass

        # Select the best code sample
        best_obj, best_sample_idx = min(objs), np.argmin(np.array(objs))
        best_code = code_runs[best_sample_idx]
        
        # Update the overall best
        if best_obj < best_obj_overall:
            best_obj_overall = best_obj
            best_code_path = code_paths[best_sample_idx]


        logging.info(f"Iteration {iter}: Min obj: {best_obj}, Best Code Path: {best_code_path}")
        logging.info(f"Iteration {iter}: GPT Output Content:\n" +  responses[best_sample_idx]["message"]["content"] + "\n")
            
        # Plot the success rate
        # fig, axs = plt.subplots(2, figsize=(6, 6))
        # fig.suptitle(f'{cfg.env.task}')

        # x_axis = np.arange(len(max_successes))

        # axs[0].plot(x_axis, np.array(max_successes))
        # axs[0].set_title("Max Success")
        # axs[0].set_xlabel("Iteration")

        # axs[1].plot(x_axis, np.array(execute_rates))
        # axs[1].set_title("Execute Rate")
        # axs[1].set_xlabel("Iteration")

        # fig.tight_layout(pad=3.0)
        # plt.savefig('summary.png')
        # np.savez('summary.npz', max_successes=max_successes, execute_rates=execute_rates, best_code_paths=best_code_paths, max_successes_reward_correlation=max_successes_reward_correlation)

        # if len(messages) == 2:
        #     messages += [{"role": "assistant", "content": responses[best_sample_idx]["message"]["content"]}]
        #     messages += [{"role": "user", "content": best_content}]
        # else:
        #     assert len(messages) == 4
        #     messages[-2] = {"role": "assistant", "content": responses[best_sample_idx]["message"]["content"]}
        #     messages[-1] = {"role": "user", "content": best_content}

        # # Save dictionary as JSON file
        # with open('messages.json', 'w') as file:
        #     json.dump(messages, file, indent=4)
    
    # Evaluate the best reward code many times
    # if max_reward_code_path is None: 
    #     logging.info("All iterations of code generation failed, aborting...")
    #     logging.info("Please double check the output env_iter*_response*.txt files for repeating errors!")
    #     exit()
    # logging.info(f"Task: {task}, Max Training Success {max_success_overall}, Correlation {max_success_reward_correlation_overall}, Best Reward Code Path: {max_reward_code_path}")
    # logging.info(f"Evaluating best reward code {cfg.num_eval} times")
    # shutil.copy(max_reward_code_path, output_file)
    
    # eval_runs = []
    # for i in range(cfg.num_eval):
    #     set_freest_gpu()
        
    #     # Execute the python file with flags
    #     stdout_filepath = f"reward_code_eval{i}.txt"
    #     with open(stdout_filepath, 'w') as f:
    #         process = subprocess.Popen(['python', '-u', f'{ISAAC_ROOT_DIR}/train.py',  
    #                                     'hydra/output=subprocess',
    #                                     f'task={task}{suffix}', f'wandb_activate={cfg.use_wandb}',
    #                                     f'wandb_entity={cfg.wandb_username}', f'wandb_project={cfg.wandb_project}',
    #                                     f'headless={not cfg.capture_video}', f'capture_video={cfg.capture_video}', 'force_render=False', f'seed={i}',
    #                                     ],
    #                                     stdout=f, stderr=f)

    #     block_until_training(stdout_filepath)
    #     eval_runs.append(process)

    # reward_code_final_successes = []
    # reward_code_correlations_final = []
    # for i, rl_run in enumerate(eval_runs):
    #     rl_run.communicate()
    #     stdout_filepath = f"reward_code_eval{i}.txt"
    #     with open(stdout_filepath, 'r') as f:
    #         stdout_str = f.read() 
    #     lines = stdout_str.split('\n')
    #     for i, line in enumerate(lines):
    #         if line.startswith('Tensorboard Directory:'):
    #             break 
    #     tensorboard_logdir = line.split(':')[-1].strip() 
    #     tensorboard_logs = load_tensorboard_logs(tensorboard_logdir)
    #     max_success = max(tensorboard_logs['consecutive_successes'])
    #     reward_code_final_successes.append(max_success)

    #     if "gt_reward" in tensorboard_logs and "gpt_reward" in tensorboard_logs:
    #         gt_reward = np.array(tensorboard_logs["gt_reward"])
    #         gpt_reward = np.array(tensorboard_logs["gpt_reward"])
    #         reward_correlation = np.corrcoef(gt_reward, gpt_reward)[0, 1]
    #         reward_code_correlations_final.append(reward_correlation)

    # logging.info(f"Final Success Mean: {np.mean(reward_code_final_successes)}, Std: {np.std(reward_code_final_successes)}, Raw: {reward_code_final_successes}")
    # logging.info(f"Final Correlation Mean: {np.mean(reward_code_correlations_final)}, Std: {np.std(reward_code_correlations_final)}, Raw: {reward_code_correlations_final}")
    # np.savez('final_eval.npz', reward_code_final_successes=reward_code_final_successes, reward_code_correlations_final=reward_code_correlations_final)


if __name__ == "__main__":
    main()