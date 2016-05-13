#!/usr/local/bin/python3.5

"""
This module executes a trace
"""

import os
import re
import json
import config
import parser
import atexit
import readline
import requests
import linecache
import threading
import itertools
import modules.sqli.sqli as sqli
import modules.filesystem.fs as fs
import modules.wrapper.sqlmap as sqlmap

from modules.logger import logger
# disable warnings when making unverified HTTPS requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

from modules.http import execute_request
from os.path import isfile, join, expanduser, dirname, realpath
from os import listdir


# global request
s = None

# global attack domain
attack_domain = ""

# specific for executing sqlmap
data_to_extract = []

def exitcleanup():
    debugMsg = "exiting {}".format(__name__)
    logger.debug(debugMsg)

# takes an attack trace and an extension matrix, and execute the attack
def execute_attack(msc_table,msc_table_info,file_aslanpp):
    global s
    global attack_domain
    logger.info("Executing the attack trace")


    atexit.register(exitcleanup)

    # general fields for sperforming an HTTP request
    url = None
    method = None

    # web application's output
    sqlmap_output = None
    sqlmap_data = None
    sqlmap_log = None
    files_output = []

    # request session object for performing subsequent HTTP requests
    s = requests.Session()

    # current response
    response = None

    # load the concretization file
    with open(config.concretization,"r") as data_file:
         concretization_data = json.load(data_file)
         data_file.close()
    concretization_domain = concretization_data["domain"]
    
    __got_cookie = False

    # loop the msc_table, the main execution loop
    for idx, row in enumerate(msc_table):
        if "<i" in row[1][0]:
            # intruder step
            tag = row[0]
            m = row[1]
            sender = m[0]
            receiver = m[1]
            message = m[2]

            debugMsg = "Message: {}".format(row)
            logger.debug(debugMsg)

            concretization_details = concretization_data[tag]

            attack_details = msc_table_info[tag]
            attack = attack_details["attack"]
            abstract_params = None
            abstract_cookies = None
            if "params" in attack_details:
                abstract_params = attack_details["params"]
            if "cookies" in attack_details:
                abstract_cookies = attack_details["cookies"]

            # if we have the keep-cookie option, we make a first empty request to
            # get the initial set-cookie
            # TODO: this request should be improved considering also the
            # parameters and the cookies
            if config.keep_cookie and not __got_cookie:
               #msc_table[
               logger.debug("executing first request for getting cookie")
               first_request = {}
               first_request["url"] = concretization_data[tag]["url"]
               first_request["method"] = concretization_data[tag]["method"]
               r = execute_request(s,first_request)
               s.cookies.clear()
               config.cookies = r.cookies
               __got_cookie = True


            # continue?
            infoMsg = "Executing {}\ncontinue?".format(row[1])
            c = __ask_yes_no(infoMsg)
            if not c:
                exit(0)


            mapping = concretization_details["mapping"] if "mapping" in concretization_details else None
            concrete_params = concretization_details["params"] if "params" in concretization_details else None
            concrete_cookies = concretization_details["cookies"] if "cookies" in concretization_details else None
            # start creating the structure used for performing attacks\requests
            req = {}
            # read the concretization file only if we are not concretizing
            # a remote shell
            req["url"] = concretization_details["url"]
            req["method"] = concretization_details["method"]
            req["params"]=concrete_params
            
            # now create the params
            # req_params = {}
            # if "params" in concretization_details:
            #     concrete_params = concretization_details["params"]
            #     for k in concrete_params:
            #         req_params = {**req_params, **concrete_params[k]}
            #     print("req params")
            #     print(req_params)
            #     req["params"] = req_params


            # start step execution
            if attack == 8:
                logger.info("Second order injection")
                logger.warning("Support for second order injection is limited")
                c = __ask_yes_no("Are you really sure you want to procede?")
                if not c:
                    logger.info("Aborting execution")
                    exit(0)
                tag_so = attack_details["tag_so"]
                so_step = None
                for item in msc_table:
                    if item[0] == tag_so:
                        so_step = item
                        debugMsg = "Exploiting so in {} and {}:{}".format(tag,tag_so,item)
                        logger.debug(debugMsg)
                        break
                req["secondOrder"] = concretization_data[tag_so]["url"]

                sqli.execute_sqlmap(req)
                continue

            # filesystem inclusion
            if attack == 4:
                logger.info("Perform file inclusion attack!")

                pages = msc_table[idx+1][1][2].split(",")
                check = concretization_data[pages[0]]

                read_file, search = __get_file_to_read(message,concretization_data)
                debugMsg = "filesystem inclusion: {} we're looking for: {}".format(read_file, search)
                logger.debug(debugMsg)
                payloads = fs.payloadgenerator(read_file)
                debugMsg = "payloads generated: {}".format(payloads)
                logger.debug(debugMsg)
                req["payloads"] = payloads
                req["ss"] = search
                wfuzz_output = fs.execute_wfuzz(req)
                if len(wfuzz_output) > 0:
                    # we successfully found something, write it on files and show them to the
                    # user. Save the file in a local structure so that they can be used in further
                    # requests
                    logger.info("saving extracted files")
                    for page in wfuzz_output:
                        url = page["url"]
                        # I should make a request and retrieve the page again
                        req["url"] = url
                        if len(page["postdata"]) > 0:
                            req["method"] = "post"
                        req["params"] = {}
                        for k,v in page["postdata"].items():
                            req["params"][k] = v
                        #__fill_parameters(abstract_params,concrete_params,req)
                        response = execute_request(s,req)
                        pathname = url.replace("http://","").replace("https://","").replace("/","_")

                        debugMsg = "Saving file {}".format(pathname)
                        logger.debug(debugMsg)

                        saved_path = fs.save_extracted_file(pathname,response.text)
                        files_output.append(saved_path)

                    logger.debug(files_output)
                    logger.info("Files have been saved")
                else:
                    # we couldn't find anything, abort execution
                    logger.critical("File inclusion did not succeed")
                    exit()
                continue


            # SQL-injection filesystem READ
            if attack == 1:
                logger.info("Perform SQLi attack for file reading!")

                real_file_to_read, search = __get_file_to_read(message, concretization_data)

                infoMsg = "file to read: {}".format(real_file_to_read)
                logger.info(infoMsg)

                req["read"] = real_file_to_read
                sqlmap_data, sqlmap_log = sqli.execute_sqlmap(req)
                debugMsg = "sqlmap_log {}".format(sqlmap_log)

                # extracted files can be found in ~/.sqlmap/output/<attacked_domani>/files/
                # list extracted file content
                tmp_files = sqli.get_list_extracted_files(attack_domain)
                logger.info("The attack performed the following result:")

                for f in tmp_files:
                    if search in open(f,"r").read():
                        infoMsg = "File {} contains the {} string".format(f,search)
                        logger.info(infoMsg)
                        files_output = files_output + f
                continue

            # SQL-injection filesystem WRITE
            if attack == 2:
                logger.info("Perform SQLi attack for file writing!")

                warningMsg = "{} makes use of sqlmap for concretization, sqlmap supports file writing only if UNION query or Stacked Query techniques can be applied. In all other cases sqlmap will fail.".format(config.TOOL_NAME)
                logger.warning(warningMsg)

                prompt = "Do you want to procede?"
                c = __ask_yes_no(prompt)
                if not c:
                    logger.info("Aborting excution")
                    exit()

                # we are uploading a remote shell for file reading
                req["write"] = config.remote_shell_write

                sqlmap_data, sqlmap_log = sqli.execute_sqlmap(req)
                debugMsg = "sqlmap_log {}".format(sqlmap_log)
                logger.debug(debugMsg)

                continue

            if attack == 10:
                # authentication bypass
                logger.info("Perform authentication bypass attack!")

                pages = msc_table[idx+1][1][2].split(",")
                check = concretization_data[pages[0]]
                is_bypassed = sqli.execute_bypass(s,req,check)
                if is_bypassed:
                    logger.info("bypass succeeded")
                else:
                    logger.info("bypass error, abort execution")
                    exit(0)


            # SQL-injection
            if attack == 0:
                # data extraction
                logger.info("Perform data extraction attack!")

                # get the table and columns to be enumarated
                exploitations = attack_details["params"]
                debugMsg = "Exploitations: {}".format(exploitations)
                logger.debug(debugMsg)

                # get the parameters to extract
                print(exploitations)
                print(attack_details["extract"])
                #for i,tag2 in enumerate(exploitations):
                #      exploit_points = exploitations[tag2]
                #      for k in exploit_points:
                #          try:
                #              tmp_map = concretization_data[tag2]["params"][k].split("=")[0]
                #          except KeyError:
                #              tmp_map = concretization_data[tag2]["cookies"][k].split("=")[0]
                #          tmp_table = concretization_data[tag2]["tables"][tmp_map]
                #          extract.append(tmp_table)

                extract = []
                tag_extract = attack_details["tag_extraction"]
                tables_to_extract = concretization_data[tag_extract]["tables"]
                for t in tables_to_extract:
                    extract.append(tables_to_extract[t])
                req["extract"] = extract
                # for the execution we need (url,method,params,data_to_extract)
                # data_to_extract => table.column
                # sqlmap_data = execute_sqlmap(url,method,params,data_to_extract)
                sqlmap_data, sqlmap_log = sqli.execute_sqlmap(req)

                sqlmap_output = sqli.sqlmap_parse_data_extracted(sqlmap_data)
                # check if the last message from sqlmap was an error or critical
                debugMsg = "sqlmap log {}".format(sqlmap_log)
                logger.debug(debugMsg)
                logger.debug(sqlmap_data)
                if not sqlmap_data:
                    logger.warning("No data extracted from the database")
                    exit()

                continue

            # exploit the sqli as a normal request
            # where we use the result from sqlmap
            if attack == 6:
                # exploiting sql-injection
                logger.info("Exploit SQLi attack")

                table = concretization_details["tables"]
                permutation_params = __product(abstract_params, concrete_params, mapping, table, sqlmap_output)
                permutation_cookies = __product(abstract_cookies, concrete_cookies, mapping, table, sqlmap_output)
                print(permutation_params)
                permutation_params = []
                found = False
                # loop on all the possibile params and cookies combination and try to exploit the result
                if len(permutation_params) == 0 and len(permutation_cookies) > 0:
                    # we only have cookies
                    print(" #### ")
                    print(len(permutation_cookies[0]))
                    print(" #### ")
                    for row in permutation_cookies:
                        for c in row:
                            if not found:
                                debugMsg = "Attempt to exploit sqli: {}".format(c)
                                logger.debug(debugMsg)
                                print(c)

                                req["cookies"] = c 
                                # req["cookies"] = dict( item.split("=") for item in header.split("%26") )
                                __fill_parameters(abstract_params, concrete_params, req)
                                response = execute_request(s,req)
                                found = __check_response(idx,msc_table,concretization_data,response)
                if not found:
                    logger.error("Exploitation failed, none of the tested parameters wored, aborting!")
                    exit(0)

                # # generate all possible combination of parameters
                # try:
                #     concretization_params = concretization_data[tag]["params"]
                #     req_params = []
                #     for k,v in concretization_params.items():
                #         tmp = v.split("=")
                #         pair = []
                #         if tmp[1] == "?":
                #            # we need to provide something from the output of sqlmap
                #            concrete_table = None
                #            try:
                #                concrete_table = concretization_data[tag]["tables"][tmp[0]].split(".")
                #            except KeyError:
                #                logger.critical("couldn't find table details in the concretization file")
                #                exit(0)
                #            extracted_values = sqlmap_data[concrete_table[0]][concrete_table[1]]
                #            for v in extracted_values:
                #                pair.append(tmp[0]+"="+v)
                #            req_params.append(pair)
                #         else:
                #             pair.append(tmp[0] + "=" + tmp[1])
                #             req_params.append(pair)
                #     debugMsg = "req_params: {}".format(req_params)
                #     logger.debug(debugMsg)
                # except KeyError:
                #     logger.warning("no parameters defined in the concretization file")

                # # generate all possible combination of cookies
                # req_cookies = []

                # abstract_cookie_to_table = {}
                # tables = concretization_data[tag]["tables"]
                # print(params)
                # print(cookies)
            

               ##  if "cookies" in concretization_data[tag]:
               ##      cookies = concretization_data[tag]["cookies"]
               ##      print(cookies)
               ##      for cookie in cookies:
               ##          print(cookie)
               ##          for c_k, c_v in cookies[cookie].items():
               ##              if "?" in c_v:
               ##                  # provide value from database

               ##              abstract_cookie_to_table[c_k] = tables[c_k]
               ##              
               ##      print(abstract_cookie_to_table)
               ##      for cookie in cookies:
                #         
                #         
                # try:
                #     concretization_cookies = concretization_data[tag]["cookies"]
                #     req_cookies = []
                #     for k,v in concretization_cookies.items():
                #         print(v)
                #         tmp = v.split("=")
                #         pair = []
                #         if tmp[1] == "?":
                #            # we need to provide something from sqlmap output
                #            concrete_table = None
                #            try:
                #                concrete_table = concretization_data[tag]["tables"][tmp[0]].split(".")
                #            except KeyError:
                #                logger.debug("coldn't find table details in the concretization file")
                #                exit(0)
                #            extracted_values = sqlmap_data[concrete_table[0]][concrete_table[1]]
                #            for v in extracted_values:
                #                pair.append(tmp[0]+"="+v)
                #            req_cookies.append(pair)
                #         else:
                #             pair.append(tmp[0] + "=" + tmp[1])
                #             req_cookies.append(pair)
                #     debugMsg = "req_cookies: {}".format(req_cookies)
                #     logger.debug(debugMsg)
                # except KeyError:
                #     logger.warning("no cookies defined in the concretization file")
                # # I used the %26 (encode of &) because it might happen that the password has a &
                # # and when I split, I split wrong
                # params_perm = []
                # cookies_perm = []
                # if len(req_params) > 0:
                #     params_perm = ["%26".join(str(y) for y in x) for x in itertools.product(*req_params)]
                # if len(req_cookies) > 0:
                #     cookies_perm = ["%26".join(str(y) for y in x) for x in itertools.product(*req_cookies)]
                # debugMsg = "params perm: {}".format(params_perm)
                # logger.debug(debugMsg)
                # debugMsg = "cookies perm: {}".format(cookies_perm)
                # logger.debug(debugMsg)

                # found = False
                # # loop on all the possibile params and cookies combination and try to exploit the result
                # if len(params_perm) == 0 and len(cookies_perm) > 0:
                #     # we only have cookies
                #     for header in cookies_perm:
                #         if not found:
                #             debugMsg = "Attempt to exploit sqli: {}".format(header)
                #             logger.debug(debugMsg)
                #             req["cookies"] = dict( item.split("=") for item in header.split("%26") )
                #             __fill_parameters(abstract_params,concrete_params,req)
                #             response = execute_request(s,req)
                #             found = __check_response(idx,msc_table,concretization_data,response)
                # elif len(params_perm) > 0 and len(cookies_perm) == 0:
                #     # we only have params
                #     for param in params_perm:
                #         if not found:
                #             debugMsg = "Attempt to exploit sqli: {}".format(param)
                #             logger.debug(debugMsg)
                #             req["params"] = dict( item.split("=") for item in param.split("%26") )
                #             __fill_parameters(abstract_params,concrete_params,req)
                #             response = execute_request(s,req)
                #             found = __check_response(idx,msc_table,concretization_data,response)
                # elif len(params_perm) > 0 and len(cookies_perm) > 0:
                #     # we have params and cookies values
                #     for param in params_perm:
                #         req["params"] = dict( item.split("=") for item in param.split("%26") )
                #         for header in cookies_perm:
                #             if not found:
                #                 debugMsg = "Attempt to exploit sqli: {}".format(param)
                #                 logger.debug(debugMsg)
                #                 req["cookies"] = dict( item.split("=") for item in header.split("%26") )
                #                 __fill_parameters(abstract_params,concrete_params,req)
                #                 response = execute_request(s,req)
                #                 found = __check_response(idx,msc_table,concretization_data,response)

                # if not found:
                #     # we couldn't procede in the trace, abort
                #     logger.warning("Exploitation failed, abort trace execution")
                #     exit(0)
                # logger.info("Exploitation succceded")
                continue

            # exploit a file upload
            if attack == 5:
                logger.info("Exploit file upload")

                # param_abstract => { abk -> abv }
                # param_mapping  => { abk -> { realk -> readv } }
                # retrieve the abstract key
                abstract_k = list(abstract_params)[0]
                abstract_v = abstract_params[abstract_k]
                if "evil_file" in abstract_v:

                    # retrieve the real key
                    real_k = list(concrete_params[abstract_k])[0]
                    req["files"] = { real_k : ("evil_script",config.EVIL_SCRIPT) }

                __fill_parameters(abstract_params,concrete_params,req)
                response = execute_request(s,req)

            # exploit filesystem attacks
            if attack == 7:
                logger.info("Exploit file-system")
                __ask_file_to_show(files_output)
                logger.debug(req["params"])
                for k,v in req["params"].items():
                    if v == "?":
                        inputMsg = "Provide value for: {}\n".format(k)
                        new_value = input(inputMsg)
                        req["params"][k] = new_value

                __fill_parameters(abstract_params,concrete_params,req)
                response = execute_request(s,req)
                found = __check_response(idx,msc_table,concretization_data,response)
                if not found:
                    logger.warning("Exploitation failed, abort trace execution")
                    exit(0)
                continue


            if attack == 9:
                logger.info("Exploiting remote shell!")

                debugMsg = "We are exploiting a remote shell for file reading {}".format(message)
                logger.debug(debugMsg)

                if req["url"] == "":
                    # we need to know the URL of the file we just uploaded
                    url_evil_file = ""
                    while url_evil_file == "":
                        url_evil_file = input("URL of the remote evil script:\n")
                    req["url"] = url_evil_file

                __fill_parameters(abstract_params,concrete_params,req)
                # perform a request to url_evil_file
                response = execute_request(s,req)
                url = req["url"]
                pathname = url.replace("http://","").replace("https://","").replace("/","_")

                saved_path = fs.save_extracted_file(pathname,response.text)
                files_output.append(saved_path)

                infoMsg = "File {} has been saved".format(saved_path)
                logger.info(infoMsg)

                continue

            # normal http request
            # we consider Forced browsing e File upload as normal requests
            if attack == -1:
                logger.info("Perform normal request")
                logger.debug(msc_table[idx][0])
                # if "params" in req:
                #     for k,v in req["params"].items():
                #         if v == "?":
                #             inputMsg = "Provide value for: {}\n".format(k)
                #             new_value = input(inputMsg)
                #             req["params"][k] = new_value
                __fill_parameters(abstract_params,concrete_params,req)
                response = execute_request(s,req)
                found = __check_response(idx,msc_table,concretization_data,response)
                logger.debug(response)
                if not found:
                    logger.critical("Response is not valid")
                    exit(0)
                logger.info("Step succeeded")
                continue

    # end loop over the msc
    logger.info("Execution of the AAT ended!")

def __fill_parameters(abstract_params,concrete_params,req):
    # if we have a ? in the params, ask the user to provide a value
    # for that parameter. Show the abstract value for better decision
    # making
    for abstract_k in abstract_params:
        real_mapping = concrete_params[abstract_k]
        for real_k in real_mapping:
            if real_mapping[real_k] == "?":
                real_v = input("provide value for parameter {} (abstract value {})\n".format(real_k,abstract_params[abstract_k]))
                req["params"][real_k] = real_v

def __ask_file_to_show(files):
    selection = ""
    while True:
        i = 0
        for f in files:
            logger.info("%d) %s", i,f)
            i = i + 1
        selection = input("Which file you want to open? (d)one\n")
        if selection == "d":
            return
        try:
            index = int(selection)
            if index < len(files):
                with open(files[int(selection)],"r") as f:
                    for line in f:
                        print(line.rstrip())
            else:
                raise Exception
        except Exception:
            logger.critical("invalid selection")

def __show_available_files(files,search):
    for f in files:
        if search in open(f,"r").read():
            logger.info("%s\t true" , f)
        else:
            logger.info("%s\t false", f)



def __get_file_to_read(message, concretization_data):
    real_file_to_retrieve = ""
    if "path_injection" in message:
        # it means we prompt the user for the filename
        real_file_to_retrieve = input("Which file yuo want to read?\n")
    else:
        # get the name of the file to retrieve from the concretization file
        abstract_file_to_retrieve = re.search(r'sqli\.([a-zA-Z]*)',message).group(1)
        real_file_to_retrieve = concretization_data["files"][abstract_file_to_retrieve]
        # and ask the user if it's ok
        c = __ask_yes_no("The file that will be read is: " + real_file_to_retrieve + ", are you sure?")
        if not c:
            # ask the user which file to retrieve
            real_file_to_retrieve = input("Which file you want to read?\n")
    # TODO: ask what regexp we should be looking for
    search = input("What are you looking for?\n")
    return real_file_to_retrieve, search

def __check_response(idx,msc_table,concretization_data,response):
    pages = msc_table[idx+1][1][2].split(",")
    p = pages[0]
    logger.debug(concretization_data[p])
    try:
            if response != None and concretization_data[p] in response.text:
                logger.debug("valid request")
                logger.debug(concretization_data[p])
                return True
    except Exception:
             return False
             logger.debug("NO ")
    return False


def __ask_yes_no(msg,default="y"):
    prompt = "[Y/n]"
    ret = True
    if default == "n":
        prompt = "[n/Y]"
        ret = False
    m = "{} {} ".format(msg,prompt)
    s = input(m)
    if s == "":
        return ret
    if s == "Y" or s == "y":
        return True
    elif s == "N" or s == "n":
        return False
    else:
        print("Invalid input");
        return __ask_yes_no(msg,default)



def __product(abstract, init, mapping, table, sqlmap_output):
    result = []
    # the following line generate an inverse mapping
    inverse_mapping = dict(zip(mapping.values(), mapping.keys()))
    for real_p in init:
        tmp = []
        if real_p in inverse_mapping:
            ab_k = inverse_mapping[real_p]
            if "tuple" in abstract[ab_k]:
                tb = table[real_p]
                possible_values = __getSQLmapValues(tb, sqlmap_output)
                for v in possible_values:
                    tmp.append({real_p:v})
        else:
            real_v = init[real_p]
            tmp.append({real_p:real_v})
        result.append(tmp)
    return result

def __getSQLmapValues(table, sqlmap_output):
    tbl = table.split(".")
    return sqlmap_output[tbl[0]][tbl[1]]

if __name__ == "__main__":
    execute_normal_request("c")

