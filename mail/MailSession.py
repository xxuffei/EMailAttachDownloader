import email
import imaplib
import logging
import time
import os

from mail import Utils
from mail.MailEML import MailEML
from mail.MailQueue import MailQueue


def parse_email_address(email_address):
    """
    Helper function to parse out the email address from the message

    return: tuple (name, address). Eg. ('John Doe', 'jdoe@example.com')
    """
    return email.utils.parseaddr(email_address)


class MailSession:
    mail_server = None
    user_name = None
    password = None
    connection = None
    error = None

    def __init__(self, mail_server, user_name, password, configuration=None):
        self.mail_server = mail_server
        self.user_name = user_name
        self.password = password
        self.connection = imaplib.IMAP4_SSL(mail_server)
        self.connection.login(user_name, password)
        self.connection.select("inbox")
        self.configuration = configuration
        self.mail_queue_cache = MailQueue()
        self.mails_index = None

    def fetch_all_mail_index(self):
        nums = None

        (result, messages) = self.connection.search(None, 'All')

        if result == "OK":
            nums = messages[0].split()
            self.mails_index = nums

        return nums

    def set_configuration(self, configuration):
        if configuration is not None:
            self.configuration = configuration

    def close_session(self):
        """
        Close the connection to the IMAP server
        """
        self.connection.close()

    def load_header(self, start_mail_index, load_mail_amount, ascending=False):
        logging.debug("load_header calling")
        temp_headers = {}
        temp_load_mail_amount = load_mail_amount
        start_load_flag = False

        temp_mails_index = self.mails_index

        if not ascending:
            temp_mails_index = list(reversed(temp_mails_index))

        for loop_index in range(len(temp_mails_index)):
            temp_mail_index = temp_mails_index[loop_index]
            if not start_load_flag and temp_mail_index == start_mail_index:
                start_load_flag = True

            if start_load_flag:
                temp_header = self.mail_queue_cache.query(temp_mail_index)

                if temp_header is None:
                    try:
                        ret, data = self.connection.fetch(temp_mail_index, '(BODY.PEEK[HEADER])')
                    except:
                        logging.debug("No new emails to read.")

                    if data[0] is not None:
                        temp_header = email.message_from_bytes(data[0][1])
                        temp_mail_eml = MailEML(header=temp_header)
                        self.mail_queue_cache.enqueue(temp_mail_index, temp_mail_eml)

                if temp_header is not None and temp_load_mail_amount != 0:
                    temp_headers[temp_mail_index] = temp_mail_eml
                    temp_load_mail_amount = temp_load_mail_amount - 1

                if temp_load_mail_amount == 0:
                    break

        logging.debug("load_header called")
        return temp_headers

    def load_mail(self, start_mail_index, load_mail_amount, ascending=False):
        logging.debug("load_mail calling")
        temp_mails = {}
        temp_load_mail_amount = load_mail_amount
        start_load_flag = False

        temp_mails_index = self.mails_index

        if not ascending:
            temp_mails_index = list(reversed(temp_mails_index))

        for loop_index in range(len(temp_mails_index)):
            temp_mail_index = temp_mails_index[loop_index]

            if not start_load_flag and temp_mail_index == start_mail_index:
                start_load_flag = True

            if start_load_flag:
                temp_mail_eml = self.mail_queue_cache.query(temp_mail_index)

                if temp_mail_eml is None or temp_mail_eml.mail is None:
                    try:
                        ret, data = self.connection.fetch(temp_mail_index, '(RFC822)')
                    except:
                        logging.debug("No new emails to read.")

                    logging.debug("%s data[0]: %s" % (str(temp_mail_index), str(data[0])))

                    if data[0] is not None:
                        temp_mail = email.message_from_bytes(data[0][1])

                        if temp_mail_eml is None:
                            temp_mail_eml = MailEML(mail=temp_mail)
                            self.mail_queue_cache.enqueue(temp_mail_index, temp_mail_eml)
                        elif temp_mail_eml.mail is None:
                            temp_mail_eml.mail = temp_mail

                if temp_mail_eml is not None and temp_load_mail_amount != 0:
                    temp_mails[temp_mail_index] = temp_mail_eml
                    temp_load_mail_amount = temp_load_mail_amount - 1

                if temp_load_mail_amount == 0:
                    break

        logging.debug("load_mail called")
        return temp_mails

    def save_attachment(self, mail_eml, download_folder="tmp", message_index=None, mail_amount=None):
        """
        Given a message, save its attachments to the specified
        download folder (default is /tmp), and update status
        information on GUI directly

        return: file path to attachment
        """
        att_path = "No attachment found."

        msg_index = message_index
        percent = int(100 * msg_index / mail_amount)
        self.configuration.status_recall(mail_eml.subject + ": ", percent)

        subject = Utils.validate_file_name(mail_eml.subject)
        folder_name = download_folder

        if self.configuration.download_in_different_folder:
            folder_name = Utils.time_tuple_to_number(mail_eml.receive_time_raw) + \
                          '-' + \
                          subject[0:20]
            folder_name = os.path.join(download_folder, folder_name)

        attach_index = 0

        for part in mail_eml.mail.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            if part.get('Content-Disposition') is None or part.get('Content-Disposition') == 'inline':
                continue

            filename_raw = part.get_filename()
            filename = email.header.decode_header(filename_raw)

            if filename[0][1] is not None:
                filename = filename[0][0].decode(filename[0][1])
            else:
                filename = filename[0][0]

            attach_index = attach_index + 1

            if not self.configuration.download_in_different_folder:
                filename = subject[0:10] + filename

            if not os.path.exists(folder_name):
                os.makedirs(folder_name)

            att_path = os.path.join(folder_name, filename)

            if not os.path.isfile(att_path):
                fp = open(att_path, 'wb')
                fp.write(part.get_payload(decode=True))
                fp.close()
                logging.info("Downloaded: " + filename)
        return att_path

    def save_mails_attachment(self, mails_index=None, download_mail_number=10):
        """
        Download attachment in mails with mails_index
        """
        emails = []

        if mails_index is None:
            mails_index = self.mails_index

        self.configuration.status_recall("附件下载中...", 0)

        loop_index = 0
        mail_amount = len(mails_index)

        for mail_index in mails_index:
            logging.debug(self.__class__.__name__ + ".save_mails_attachment: mail_index = " + str(mail_index))

            if loop_index == download_mail_number:
                break

            mail_eml = self.mail_queue_cache.query(mail_index)

            if mail_eml is None:
                logging.debug(
                    self.__class__.__name__ + ".save_mails_attachment: mail_index = %s is not cached" %
                    str(mail_index))
                mail_eml = self.load_mail(mail_index, 1)[mail_index]
            elif mail_eml.mail is None:
                logging.debug(
                    self.__class__.__name__ + ".save_mails_attachment: mail_index = %s body is not cached" %
                    str(mail_index))
                mail_eml = self.load_mail(mail_index, 1)[mail_index]
            else:
                logging.debug(
                    self.__class__.__name__ + ".save_mails_attachment: mail_index = %s is cached" %
                    str(mail_index))

            if mail_eml.mail is None:
                logging.debug(
                    self.__class__.__name__ + ".save_mails_attachment: mail_index = %s fetch None mail body" %
                    str(mail_index))
            else:
                self.save_attachment(mail_eml,
                                     self.configuration.download_folder,
                                     message_index=loop_index,
                                     mail_amount=mail_amount)

            loop_index = loop_index + 1
            percent = int(100 * loop_index / mail_amount)
            self.configuration.status_recall(percent=percent)

        self.configuration.status_recall("附件下载完成！", 100)

        return emails
