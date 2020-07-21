import os
from parsons.aws.s3 import S3
import time
import logging

logger = logging.getLogger(__name__)

S3_TEMP_KEY_PREFIX = "Parsons_RedshiftCopyTable"

# Used to optimize the splitting of tables for the copy.
CLUSTER_SLICES = 10
MINIMUM_FILE_SPLIT = 100000000000
# MINIMUM_FILE_SPLIT = 1


class RedshiftCopyTable(object):

    aws_access_key_id = None
    aws_secret_access_key = None
    iam_role = None

    def __init__(self):

        pass

    def copy_statement(self, table_name, bucket, key, manifest=False,
                       data_type='csv', csv_delimiter=',', max_errors=0,
                       statupdate=True, compupdate=True, ignoreheader=1, acceptanydate=True,
                       dateformat='auto', timeformat='auto', emptyasnull=True,
                       blanksasnull=True, nullas=None, acceptinvchars=True, truncatecolumns=False,
                       specifycols=None, aws_access_key_id=None, aws_secret_access_key=None,
                       compression=None):

        # Source / Destination
        source = f's3://{bucket}/{key}'

        # Add column list for mapping or if there are fewer columns on source file
        col_list = f"({', '.join(specifycols)})" if specifycols is not None else ""

        sql = f"copy {table_name}{col_list} \nfrom '{source}' \n"

        # Generate credentials
        sql += self.get_creds(aws_access_key_id, aws_secret_access_key)

        # Other options
        if manifest:
            sql += "manifest \n"
        sql += f"maxerror {max_errors} \n"
        if statupdate:
            sql += "statupdate on\n"
        if compupdate:
            sql += "compupdate on \n"
        else:
            sql += "compupdate off \n"
        if ignoreheader:
            sql += f"ignoreheader {ignoreheader} \n"
        if acceptanydate:
            sql += "acceptanydate \n"
        sql += f"dateformat '{dateformat}' \n"
        sql += f"timeformat '{timeformat}' \n"
        if emptyasnull:
            sql += "emptyasnull \n"
        if blanksasnull:
            sql += "blanksasnull \n"
        if nullas:
            sql += f"null as {nullas}"
        if acceptinvchars:
            sql += "acceptinvchars \n"
        if truncatecolumns:
            sql += "truncatecolumns \n"

        # Data Type
        if data_type == 'csv':
            sql += f"csv delimiter '{csv_delimiter}' \n"
        else:
            raise TypeError('Invalid data type specified.')

        if compression == 'gzip':
            sql += 'gzip \n'

        sql += ';'

        return sql

    def get_creds(self, aws_access_key_id, aws_secret_access_key):

        if aws_access_key_id and aws_secret_access_key:
            # When we have credentials, then we don't need to set them again
            pass

        elif self.iam_role:
            # bail early, since the bottom is specifically formatted with creds
            return f"credentials 'aws_iam_role={self.iam_role}'\n"

        elif self.aws_access_key_id and self.aws_secret_access_key:

            aws_access_key_id = self.aws_access_key_id
            aws_secret_access_key = self.aws_secret_access_key

        elif 'AWS_ACCESS_KEY_ID' in os.environ and 'AWS_SECRET_ACCESS_KEY' in os.environ:

            aws_access_key_id = os.environ['AWS_ACCESS_KEY_ID']
            aws_secret_access_key = os.environ['AWS_SECRET_ACCESS_KEY']

        else:

            s3 = S3()
            creds = s3.aws.session.get_credentials()
            aws_access_key_id = creds.access_key
            aws_secret_access_key = creds.secret_key

        return "credentials 'aws_access_key_id={};aws_secret_access_key={}'\n".format(
            aws_access_key_id,
            aws_secret_access_key)

    def temp_s3_copy(self, tbl, aws_access_key_id=None, aws_secret_access_key=None):

        if not self.s3_temp_bucket:
            raise KeyError(("Missing S3_TEMP_BUCKET, needed for transferring data to Redshift. "
                            "Must be specified as env vars or kwargs"
                            ))

        # Coalesce S3 Key arguments
        aws_access_key_id = aws_access_key_id or self.aws_access_key_id
        aws_secret_access_key = aws_secret_access_key or self.aws_secret_access_key

        self.s3 = S3(aws_access_key_id=aws_access_key_id,
                     aws_secret_access_key=aws_secret_access_key)

        hashed_name = hash(time.time())
        key = f"{S3_TEMP_KEY_PREFIX}/{hashed_name}.csv.gz"

        file_path = tbl.to_csv(temp_file_compression='gzip')
        tbl_list = self.split_table(tbl, file_path)

        print (len(tbl_list))

        for idx, tbl in enumerate(tbl_list):
            # Convert table to compressed CSV file, to optimize the transfers to S3 and to
            # Redshift.
            
            file_path = tbl.to_csv()
            # file_path = tbl.to_csv(temp_file_compression='gzip')
            # Copy table to bucket
            self.s3.put_file(self.s3_temp_bucket, key + f".{str(idx)}", file_path)

        return key

    def temp_s3_delete(self, key):

        for keys in self.s3.list_keys(self.s3_temp_bucket, prefix=key):
            self.s3.remove_file(self.s3_temp_bucket, key)

    def split_table(self, tbl, file_path):

        if os.stat(file_path).st_size >= MINIMUM_FILE_SPLIT:
            tbl_list = tbl.chunk(int(tbl.num_rows / CLUSTER_SLICES))
            print ('split...')
            return tbl_list
        else:
            print ('no split...')
            return [tbl]
